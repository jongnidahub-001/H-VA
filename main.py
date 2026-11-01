from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import time
import uuid
import os
import pandas as pd
from datetime import datetime
from docx import Document
import zipfile
from urllib.parse import quote
import urllib.error
import urllib.request
from graph import Graph, SentimentAnalysis, AdminAssistant, EmotionAssistant, GeminiKeyMissing, get_llm
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
import settings
from storage import store

# Initialize FastAPI app
app = FastAPI(title="Nida Bot Customer Complaint Chatbot", version="1.0.0")

@app.exception_handler(GeminiKeyMissing)
async def gemini_key_missing(request: Request, exc: GeminiKeyMissing):
    return JSONResponse({"detail": "Gemini API key is not set. Ask an admin to set it on the admin site."},
                        status_code=503)

# Startup event to clean up expired sessions (on Cloud Storage a lifecycle rule does this)
@app.on_event("startup")
async def startup_event():
    store.delete_older_than("chat_history/", max_age_hours=24)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize components
df = pd.read_csv('customer_complaints.csv')
df_domain = df.drop_duplicates(subset=['Domain'])
df_domain = df_domain['Domain'].values.tolist()

graph = Graph()
sentiment = SentimentAnalysis()
assistant = AdminAssistant()
emotion = EmotionAssistant()

# Accounts and passwords come from the environment (Secret Manager on Cloud Run, .env locally).
# An account whose password is unset cannot log in.
ACCOUNTS = {
    os.getenv("ADMIN_USERNAME", "admin"): ("admin", os.getenv("ADMIN_PASSWORD", "")),
    os.getenv("USER_USERNAME", "user"): ("user", os.getenv("USER_PASSWORD", "")),
}
# Without SECRET_KEY, logins only last until the process restarts
SECRET_KEY = (os.getenv("SECRET_KEY") or secrets.token_hex(32)).encode()
SESSION_COOKIE = "nidabot_session"
SESSION_MAX_AGE = 12 * 3600

def password_matches(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())

def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()

def make_token(username: str, role: str) -> str:
    data = json.dumps({"u": username, "r": role, "exp": int(time.time()) + SESSION_MAX_AGE})
    payload = base64.urlsafe_b64encode(data.encode()).decode()
    return f"{payload}.{_sign(payload)}"

def read_token(token: Optional[str]) -> Optional[Dict[str, str]]:
    """Return {"username", "role"} for a valid, unexpired token, else None"""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return {"username": data["u"], "role": data["r"]}

def current_user(request: Request) -> Optional[Dict[str, str]]:
    return read_token(request.cookies.get(SESSION_COOKIE))

PUBLIC_API = {"/api/login", "/api/logout"}

@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        user = current_user(request)
        if user is None:
            return JSONResponse({"detail": "Not logged in"}, status_code=401)
        if path.startswith("/api/admin/") and user["role"] != "admin":
            return JSONResponse({"detail": "Admin only"}, status_code=403)
    return await call_next(request)

# Helper function to sanitize message names
def sanitize_name(name: str) -> str:
    """
    Sanitize a name to comply with OpenAI's name pattern requirements.
    Pattern: ^[^\s<|\\/>]+$ (no whitespace or special characters)
    """
    if not name:
        return "Chatbot"
    # Replace spaces with underscores and remove problematic characters
    sanitized = name.replace(' ', '_').replace('(', '').replace(')', '').replace('<', '').replace('>', '').replace('|', '').replace('\\', '').replace('/', '')
    return sanitized if sanitized else "Chatbot"

# Pydantic models
class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant' or 'system'
    content: str
    name: Optional[str] = None
    timestamp: Optional[str] = None

class ChatSession(BaseModel):
    session_id: str
    messages: List[ChatMessage] = []
    config: Dict[str, Any] = {}
    start_time: Optional[str] = None
    ai_enabled: bool = False

class ConfigUpdate(BaseModel):
    language: str = "Thai"
    domain: str
    job_position: str
    difficulty: str = "Common Case"
    reform: str = "Assertive Leadership"
    agent_name: Optional[str] = None

class AuthRequest(BaseModel):
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class EvaluationRequest(BaseModel):
    session_id: str

# Session storage: one JSON document per chat session
class SessionManager:
    @staticmethod
    def _key(session_id: str) -> Optional[str]:
        try:
            return f"chat_history/{uuid.UUID(session_id)}.json"
        except ValueError:
            return None

    def create_session(self, session_id: str) -> ChatSession:
        session = ChatSession(session_id=session_id)
        self.update_session(session)
        return session

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        key = self._key(session_id)
        data = store.get(key) if key else None
        return ChatSession(**data) if data else None

    def update_session(self, session: ChatSession):
        store.put(self._key(session.session_id), session.dict())

session_manager = SessionManager()

# Case difficulty prompts
common_case_prompt = """
    1.Common Case
        Definition:
            A common case involves typical customer complaints or concerns that occur frequently and are straightforward to resolve. These cases usually stem from minor misunderstandings or simple issues such as delayed orders, basic inquiries, or clarification requests.
        Characteristics:
            Use very polite words and phrases.
            Clear and simple communication from customers.
            Low emotional intensity and easy to calm.
            Customers cooperative and receptive to guidance.
            Problems typically resolved quickly and efficiently.
"""

uncommon_case_prompt = """
    2.Uncommon Case
        Definition:
            An uncommon case presents more complexity due to atypical issues or complications in products or services, often requiring deeper investigation or coordination among departments. Customers may demonstrate moderate frustration, stubbornness, or dissatisfaction, requiring enhanced interpersonal skills and problem-solving capabilities from customer service.

        Characteristics:
            Customer exhibits moderate frustration and stubbornness.
            Issues require additional investigation or interdepartmental coordination.
            Customer may initially resist proposed solutions.
            Communication is more challenging but can be managed through patience and detailed explanations.
"""

extreme_case_prompt = """
    3.Extreme Case
        Definition:
            An extreme case involves significant emotional escalation, characterized by high customer anger, rudeness, hostility, and initial unwillingness to engage rationally or cooperatively with customer service agents. The customer often resists listening or responding constructively until effectively calmed and managed through specialized interpersonal and conflict resolution skills.

        Characteristics:
            High emotional intensity; customer exhibits anger, rudeness, hostility.
            Customer may initially refuse to listen or cooperate.
            Significant effort required from customer service to de-escalate emotions.
            Solutions demand exceptional interpersonal, empathy, and conflict management skills.
"""

def get_difficulty_prompt(difficulty: str) -> str:
    if difficulty == "Common Case":
        return common_case_prompt
    elif difficulty == "Uncommon Case":
        return uncommon_case_prompt
    elif difficulty == "Extreme Case":
        return extreme_case_prompt
    return common_case_prompt

# Evaluation function
class Evaluation(BaseModel):
    response_accuracy: int = Field(description="Response Accuracy from 1 to 5")
    response_time: int = Field(description="Response Time from 1 to 5")
    empathy_and_tone: int = Field(description="Empathy and Tone from 1 to 5")
    clarity: int = Field(description="Clarity from 1 to 5")
    professionalism: int = Field(description="Professionalism from 1 to 5")
    comment: str = Field(description="Comment for the evaluation")

def evaluate_case_dialog(chat_history, total_time):
    model = get_llm(settings.eval_model()).with_structured_output(Evaluation)
    
    prompt = """
        You are an expert Agent Performance Evaluator. Provide a rating from 1-5 for each of the following criteria based on the chat history and total resolution time provided.  Justify each rating with SPECIFIC examples from the chat history.

        1. Accuracy & Policy Adherence:
        Definition:  The correctness and completeness of information provided AND whether the agent correctly followed company policies.
        Rating (1-5):
            1/5: Information was completely incorrect or unrelated, AND the agent demonstrably violated company policy.
            2/5: Information was partially correct with significant errors/omissions OR the agent made a minor policy error.
            3/5: Mostly correct information with minor inaccuracies OR a very slight deviation from best practices.
            4/5: Fully correct information, addressing the core issue effectively AND followed all relevant policies.
            5/5: Completely accurate, comprehensive information, perfectly resolved the customer's issue AND flawlessly adhered to all applicable company policies.

        2. Responsiveness (Speed & Efficiency):
        Definition:  The promptness of the interaction, considering BOTH first response time AND total resolution time, relative to expected standards.
        Rating (1-5):
            1/5:  If total time is more than 30 minutes, then rating is 1/5.
            2/5:  if total time is more than 25 minutes, then rating is 2/5.
            3/5:  if total time is more than 20 minutes, then rating is 3/5.
            4/5:  if total time is more than 15 minutes, then rating is 4/5.
            5/5:  if total time is less than 10 minutes, then rating is 5/5.

        3. Clarity & Completeness:
        Definition: Simplicity, comprehensibility, ease of understanding, AND completeness of the information provided.
        Rating (1-5):
            1/5:  Communication was confusing, unclear, difficult to understand, AND lacked essential information.
            2/5:  Somewhat unclear or vague, leaving the customer confused OR missing key information.
            3/5:  Generally clear but included some ambiguous statements or unnecessary complexity OR missed a minor piece of information.
            4/5:  Clear, concise, easy to understand, AND provided all essential information for the customer to proceed.
            5/5: Exceptionally clear, straightforward, immediately understandable, AND anticipated potential customer questions.

        4. Empathy & Acknowledgment of Inconvenience:
        Definition: Demonstration of emotional understanding, genuine care, AND explicit acknowledgment of any inconvenience caused to the customer.
        Rating (1-5):
            1/5:  No empathy shown; responses felt dismissive or indifferent AND no acknowledgment of the customer's issue.
            2/5:  Limited empathy, superficially acknowledged the issue without genuine concern.
            3/5:  Adequate empathy, showing general concern, but lacked deep emotional understanding or specific acknowledgment of inconvenience.
            4/5:  Strong empathy, clearly acknowledged the customer's feelings and concerns with genuine care AND specifically acknowledged the inconvenience.
            5/5: Outstanding empathy, fully understood the customer's emotional state, showed exceptional sensitivity, AND went above and beyond to compensate for the inconvenience.

        5. Professionalism & Brand Representation:
        Definition: Courtesy, politeness, respect, appropriate conduct, AND positive representation of the organization's values and brand image.
        Rating (1-5):
            1/5:  Highly unprofessional; rude, disrespectful, inappropriate behavior, AND negatively impacted the company's image.
            2/5:  Somewhat unprofessional; mild discourtesy or lack of politeness OR inconsistent with brand voice.
            3/5:  Sufficiently professional, maintaining basic courtesy but without exceptional politeness or warmth AND neutral brand representation.
            4/5:  Notably professional; consistently courteous, respectful, and appropriate throughout the interaction AND positively represented the company.
            5/5: Impeccably professional; demonstrated exceptional politeness, courtesy, respectfulness, appropriate conduct, AND consistently reinforced the company's values and brand image.

        Here is the chat history for the case:
        {chat_history}

        Here is the total time to resolve the case:
        {total_time}

    """.format(chat_history=chat_history, total_time=total_time)

    response = model.invoke(prompt)
    return response

# Pages depend on who is logged in, so the browser must not reuse a cached copy
NO_STORE = {"Cache-Control": "no-store"}

def page(path: str) -> FileResponse:
    return FileResponse(path, headers=NO_STORE)

def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303, headers=NO_STORE)

# Routes
@app.get("/")
def read_root(request: Request):
    user = current_user(request)
    if user is None:
        return redirect("/login")
    # The admin account has its own site for settings and reports, not the chat
    if user["role"] == "admin":
        return redirect("/admin")
    return page('static/index.html')

@app.get("/admin")
def admin_page(request: Request):
    user = current_user(request)
    if user is None:
        return redirect("/login")
    if user["role"] != "admin":
        return redirect("/")
    return page('static/admin.html')

@app.get("/login")
def login_page(request: Request):
    if current_user(request) is not None:
        return redirect("/")
    return page('static/login.html')

@app.post("/api/login")
def login(body: LoginRequest, request: Request, response: Response):
    role, password = ACCOUNTS.get(body.username, (None, ""))
    if role is None or not password_matches(body.password, password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(SESSION_COOKIE, make_token(body.username, role), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="lax", secure=https)
    return {"username": body.username, "role": role}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"message": "Logged out"}

@app.get("/api/me")
def me(request: Request):
    return current_user(request)

@app.get("/api/domains")
def get_domains():
    return {"domains": df_domain}

@app.get("/api/job-positions/{domain}")
def get_job_positions(domain: str):
    positions = df[df['Domain'] == domain]['Job Position'].unique().tolist()
    return {"job_positions": positions}

@app.get("/api/complaint-detail/{domain}/{job_position}")
def get_complaint_detail(domain: str, job_position: str):
    data = df[(df['Domain'] == domain) & (df['Job Position'] == job_position)]
    if data.empty:
        raise HTTPException(status_code=404, detail="Complaint detail not found")
    
    detail = {
        "domain": data['Domain'].values[0],
        "job_position": data['Job Position'].values[0],
        "category": data['Category'].values[0],
        "complaint": data['Complaint'].values[0]
    }
    return detail

@app.post("/api/auth")
def authenticate(auth_request: AuthRequest):
    """Check the separate password that turns on the AI features"""
    if password_matches(auth_request.password, settings.ai_password()):
        return {"authenticated": True}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.post("/api/sessions")
def create_session():
    session_id = str(uuid.uuid4())
    session_manager.create_session(session_id)
    return {"session_id": session_id}

@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@app.put("/api/sessions/{session_id}/config")
def update_session_config(session_id: str, config: ConfigUpdate):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Get complaint detail
    data = df[(df['Domain'] == config.domain) & (df['Job Position'] == config.job_position)]
    if data.empty:
        raise HTTPException(status_code=404, detail="Complaint detail not found")
    
    domain_detail = f"**Complaint Detail** :  \n  **Domain** : {data['Domain'].values[0]}  \n  **Job Position** : {data['Job Position'].values[0]}  \n  **Category** : {data['Category'].values[0]}  \n  **Complaint** : {data['Complaint'].values[0]}"
    
    session.config = {
        "language": config.language,
        "domain": config.domain,
        "job_position": config.job_position,
        "difficulty": config.difficulty,
        "reform": config.reform,
        "agent_name": config.agent_name,
        "domain_detail": domain_detail,
        "difficult_prompt": get_difficulty_prompt(config.difficulty)
    }
    
    session_manager.update_session(session)
    return {"message": "Configuration updated successfully"}

@app.post("/api/sessions/{session_id}/start")
async def start_session(session_id: str):
    session = await run_in_threadpool(session_manager.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.start_time = datetime.now().isoformat()
    
    # Initialize with system messages
    workflow = graph.workflow(
        difficulty=session.config.get("difficulty", "Common Case"),
        language=session.config.get("language", "Thai")
    )
    
    system_messages = [
        SystemMessage(content='Here is the domain using for generate situation : ' + session.config.get("domain_detail", "")),
        SystemMessage(content='Here is the character of conversation : ' + session.config.get("difficult_prompt", "")),
        HumanMessage(content="start")
    ]
    
    # Get initial bot response
    messages = []
    async for m in workflow.astream({"messages": system_messages}, stream_mode='updates'):
        for key, value in m.items():
            if value["messages"][-1].content != "start":
                messages.append(ChatMessage(
                    role="assistant",
                    content=value["messages"][-1].content,
                    name=sanitize_name(key),
                    timestamp=datetime.now().isoformat()
                ))
    
    session.messages = messages
    await run_in_threadpool(session_manager.update_session, session)
    return {"message": "Session started", "messages": messages}

@app.post("/api/sessions/{session_id}/chat")
async def send_message(session_id: str, message: ChatMessage):
    session = await run_in_threadpool(session_manager.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Add user message
    user_message = ChatMessage(
        role="user",
        content=message.content,
        timestamp=datetime.now().isoformat()
    )
    session.messages.append(user_message)
    
    # Convert to langchain messages
    langchain_messages = []
    for msg in session.messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content, name=sanitize_name(msg.name or "Chatbot")))
    
    # Get bot response
    workflow = graph.workflow(
        difficulty=session.config.get("difficulty", "Common Case"),
        language=session.config.get("language", "Thai")
    )
    
    system_messages = [
        SystemMessage(content='Here is the domain using for generate situation : ' + session.config.get("domain_detail", "")),
        SystemMessage(content='Here is the character of conversation : ' + session.config.get("difficult_prompt", ""))
    ]
    
    bot_messages = []
    async for m in workflow.astream({"messages": system_messages + langchain_messages}, stream_mode='updates'):
        for key, value in m.items():
            bot_message = ChatMessage(
                role="assistant",
                content=value["messages"][-1].content,
                name=sanitize_name(key),
                timestamp=datetime.now().isoformat()
            )
            session.messages.append(bot_message)
            bot_messages.append(bot_message)
            langchain_messages.append(AIMessage(content=value["messages"][-1].content, name=sanitize_name(key)))
    
    # Save session after adding messages
    await run_in_threadpool(session_manager.update_session, session)
    
    # Get sentiment analysis
    sentiment_score = 0
    suggestions = []
    emotion_advice = ""
    
    if session.ai_enabled and bot_messages:
        latest_message = bot_messages[-1].content
        sentiment_score = await run_in_threadpool(sentiment.get_sentiment, latest_message)
        
        # Get suggestions
        try:
            suggestion_results = await run_in_threadpool(assistant.get_suggest, langchain_messages, session.config.get("language", "Thai"))
            suggestions = [s.content for s in suggestion_results]
            print(f"Admin suggestions for session {session_id}: {suggestions}")
        except Exception as e:
            print(f"Error getting admin suggestions: {str(e)}")
            suggestions = []
        
        # Get emotion advice
        try:
            emotion_advice = await run_in_threadpool(
                emotion.get_suggest,
                latest_message,
                session.config.get("reform", "Assertive Leadership"),
                session.config.get("language", "Thai")
            )
            print(f"Emotion advice for session {session_id}: {emotion_advice}")
        except Exception as e:
            print(f"Error getting emotion advice: {str(e)}")
            emotion_advice = ""
    
    return {
        "messages": bot_messages,
        "sentiment_score": sentiment_score,
        "suggestions": suggestions,
        "emotion_advice": emotion_advice
    }

@app.get("/api/sessions/{session_id}/sentiment")
def get_sentiment(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.messages:
        return {"sentiment_score": 0}
    
    # Get last assistant message
    last_message = None
    for msg in reversed(session.messages):
        if msg.role == "assistant":
            last_message = msg.content
            break
    
    if last_message:
        score = sentiment.get_sentiment(last_message)
        return {"sentiment_score": score}
    
    return {"sentiment_score": 0}

@app.put("/api/sessions/{session_id}/ai-enable")
def enable_ai(session_id: str, enabled: bool):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session.ai_enabled = enabled
    session_manager.update_session(session)
    return {"message": f"AI {'enabled' if enabled else 'disabled'} successfully"}

@app.post("/api/sessions/{session_id}/evaluate")
def evaluate_session(session_id: str, request: EvaluationRequest):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Get agent name from session config, with fallback to "Unknown"
    agent_name = session.config.get("agent_name", "Unknown")
    
    # Calculate elapsed time
    finish_time = datetime.now()
    if session.start_time:
        start_time = datetime.fromisoformat(session.start_time)
        elapsed_minutes = (finish_time - start_time).total_seconds() / 60
        # Convert to hours for Excel sorting (decimal format)
        total_time_hours = elapsed_minutes / 60
    else:
        start_time = None
        elapsed_minutes = 0
        total_time_hours = 0
    
    # Convert messages to evaluation format
    chat_history_str = ""
    for msg in session.messages:
        if msg.role == "user":
            chat_history_str += f"**User** : {msg.content}\n"
        elif msg.role == "assistant":
            chat_history_str += f"**Admin** : {msg.content}\n"
    
    try:
        response = evaluate_case_dialog(chat_history_str, f"Total Time : {elapsed_minutes:.2f} minutes")
        
        # Save evaluation result
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        # \w alone would drop Thai vowel and tone marks
        safe_agent = re.sub(r"[^\w\u0E00-\u0E7F-]", "_", agent_name or "Unknown")[:50]
        filename = f"{safe_agent}_{timestamp}_{unique_id}.json"
        
        evaluation_data = {
            "agent_name": agent_name,
            "ai_enabled": session.ai_enabled,
            "timestamp": datetime.now().isoformat(),
            "evaluation_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "start_time": start_time.isoformat() if start_time else None,
            "finish_time": finish_time.isoformat(),
            "total_time_minutes": elapsed_minutes, # 4 decimal places for precision
            "scores": {
                "response_accuracy": response.response_accuracy,
                "response_time": response.response_time,
                "empathy_and_tone": response.empathy_and_tone,
                "clarity": response.clarity,
                "professionalism": response.professionalism
            },
            "comment": response.comment,
            "chat_history": chat_history_str,
            "messages": [m.dict() for m in session.messages],
            "config": {k: session.config.get(k) for k in ("language", "domain", "job_position", "difficulty", "reform")},
            "case_id": session_id
        }
        
        store.put(f"score/{filename}", evaluation_data)
        
        return {
            "evaluation": evaluation_data,
            "filename": filename
        }
    except GeminiKeyMissing:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")

def load_scores(ai_enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
    scores = []
    for _, data in store.items("score/"):
        for item in (data if isinstance(data, list) else [data]):
            if ai_enabled is None or item.get("ai_enabled") == ai_enabled:
                scores.append(item)
    return scores

def build_reports_zip(merged_data: List[Dict[str, Any]], label: str, timestamp: str) -> bytes:
    """Excel score sheet + Word chat history summary, zipped in memory"""
    # Create Excel file with flattened score columns
    excel_data = []
    for item in merged_data:
        # Flatten the scores into separate columns
        row = {
            'agent_name': item.get('agent_name', 'Unknown'),
            'evaluation_date': item.get('evaluation_date', ''),
            'start_time': item.get('start_time', ''),
            'finish_time': item.get('finish_time', ''),
            'total_time_minutes': item.get('total_time_minutes', 0),
            'case_id': item.get('case_id', ''),
            'comment': item.get('comment', '')
        }

        # Add individual score columns
        scores = item.get('scores', {})
        row['response_accuracy'] = scores.get('response_accuracy', 0)
        row['response_time'] = scores.get('response_time', 0)
        row['empathy_and_tone'] = scores.get('empathy_and_tone', 0)
        row['clarity'] = scores.get('clarity', 0)
        row['professionalism'] = scores.get('professionalism', 0)

        # Calculate average score
        score_values = [
            scores.get('response_accuracy', 0),
            scores.get('response_time', 0),
            scores.get('empathy_and_tone', 0),
            scores.get('clarity', 0),
            scores.get('professionalism', 0)
        ]
        row['average_score'] = sum(score_values) / len(score_values) if score_values else 0

        excel_data.append(row)

    # Create DataFrame and save to Excel
    df = pd.DataFrame(excel_data)
    excel_buffer = io.BytesIO()

    # Save to Excel with formatting
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Agent Scores', index=False)

        # Get the workbook and worksheet
        workbook = writer.book
        worksheet = writer.sheets['Agent Scores']

        # Auto-adjust column widths
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width

    # Create Word document with chat history summary
    # Group data by agent
    agents_data = {}
    for item in merged_data:
        agent_name = item.get('agent_name', 'Unknown')
        if agent_name not in agents_data:
            agents_data[agent_name] = []
        agents_data[agent_name].append(item)

    # Create Word document
    doc = Document()
    doc.add_heading('Agent Chat History Summary Report', 0)

    # Add generation timestamp
    doc.add_paragraph(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    doc.add_paragraph()

    # Add summary statistics
    doc.add_heading('Summary Statistics', level=1)
    total_sessions = len(merged_data)
    total_agents = len(agents_data)
    doc.add_paragraph(f"Total Sessions: {total_sessions}")
    doc.add_paragraph(f"Total Agents: {total_agents}")
    doc.add_paragraph()

    # Add detailed information for each agent
    for agent_name, sessions in agents_data.items():
        doc.add_heading(f'Agent: {agent_name}', level=1)
        doc.add_paragraph(f"Total Sessions: {len(sessions)}")

        # Calculate agent statistics
        scores_list = []
        total_time = 0
        for session in sessions:
            scores = session.get('scores', {})
            if scores:
                avg_score = sum(scores.values()) / len(scores)
                scores_list.append(avg_score)
            total_time += session.get('total_time_minutes', 0)

        if scores_list:
            avg_agent_score = sum(scores_list) / len(scores_list)
            doc.add_paragraph(f"Average Score: {avg_agent_score:.2f}/5")

        avg_time = total_time / len(sessions) if sessions else 0
        doc.add_paragraph(f"Average Session Time: {avg_time:.2f} minutes")
        doc.add_paragraph()

        # Add individual session details
        doc.add_heading('Session Details:', level=2)
        for i, session in enumerate(sessions, 1):
            doc.add_heading(f'Session {i} - {session.get("evaluation_date", "Unknown Date")}', level=3)

            # Session info
            doc.add_paragraph(f"Case ID: {session.get('case_id', 'Unknown')}")
            doc.add_paragraph(f"Duration: {session.get('total_time_minutes', 0):.2f} minutes")

            # Scores
            scores = session.get('scores', {})
            if scores:
                doc.add_paragraph("Scores:")
                for score_type, score_value in scores.items():
                    score_name = score_type.replace('_', ' ').title()
                    doc.add_paragraph(f"  • {score_name}: {score_value}/5", style='List Bullet')

            # Comment
            comment = session.get('comment', '')
            if comment:
                doc.add_paragraph("Evaluation Comment:")
                doc.add_paragraph(comment)

            # Chat history
            chat_history = session.get('chat_history', '')
            if chat_history:
                doc.add_heading('Chat History:', level=4)
                # Split chat history into manageable paragraphs
                chat_lines = chat_history.split('\n')
                for line in chat_lines:
                    if line.strip():
                        if line.startswith('**User**'):
                            doc.add_paragraph(line, style='List Bullet')
                        elif line.startswith('**Admin**'):
                            doc.add_paragraph(line, style='List Bullet 2')
                        else:
                            doc.add_paragraph(line)

            doc.add_paragraph()  # Add space between sessions

        doc.add_page_break()  # New page for each agent


    doc_buffer = io.BytesIO()
    doc.save(doc_buffer)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(f"agent_scores_report_{label}_{timestamp}.xlsx", excel_buffer.getvalue())
        zipf.writestr(f"agent_chat_history_report_{label}_{timestamp}.docx", doc_buffer.getvalue())
    return zip_buffer.getvalue()

@app.get("/api/admin/summary")
def admin_summary():
    """Counts shown in the admin panel"""
    scores = load_scores()
    dates = [s.get("evaluation_date", "") for s in scores if s.get("evaluation_date")]
    return {
        "total": len(scores),
        "ai_enabled": sum(1 for s in scores if s.get("ai_enabled")),
        "ai_disabled": sum(1 for s in scores if not s.get("ai_enabled")),
        "agents": len({s.get("agent_name", "Unknown") for s in scores}),
        "latest": max(dates) if dates else None,
    }

@app.get("/api/admin/generate-reports/{ai_enabled}")
def generate_and_download_reports(ai_enabled: str):
    """Build the report ZIP for cases with or without AI and send it directly"""
    if ai_enabled.lower() not in ("true", "false"):
        raise HTTPException(status_code=400, detail="ai_enabled must be 'true' or 'false'")
    ai_enabled_bool = ai_enabled.lower() == "true"

    merged_data = load_scores(ai_enabled_bool)
    if not merged_data:
        raise HTTPException(status_code=404, detail="No score data found")

    label = 'ai_enabled' if ai_enabled_bool else 'ai_disabled'
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        content = build_reports_zip(merged_data, label, timestamp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating reports: {str(e)}")

    zip_filename = f"agent_reports_{label}_{timestamp}.zip"
    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'})

# ---------------------------------------------------------------------------
# Admin: settings (Gemini API key, AI password)
# ---------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    gemini_api_key: Optional[str] = None
    clear_gemini_api_key: bool = False
    # "" goes back to the deployment model
    chat_model: Optional[str] = None
    eval_model: Optional[str] = None
    ai_password: Optional[str] = None
    clear_ai_password: bool = False

class KeyTestRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    model: Optional[str] = None

MODEL_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{1,80}$")

def mask_secret(value: str) -> str:
    return f"••••{value[-4:]}" if len(value) > 8 else "••••"

@app.get("/api/admin/settings")
def get_settings():
    saved = settings.load(force=True)
    key = settings.gemini_api_key()
    return {
        "gemini_api_key": {"set": bool(key), "masked": mask_secret(key) if key else "",
                           "source": settings.gemini_api_key_source()},
        "ai_password": {"value": settings.ai_password(), "source": settings.ai_password_source()},
        "models": {
            "chat": settings.chat_model(),
            "evaluation": settings.eval_model(),
            "chat_from_admin": bool(saved.get("chat_model")),
            "evaluation_from_admin": bool(saved.get("eval_model")),
            "deployment_chat": settings.deployment_chat_model(),
            "deployment_evaluation": settings.deployment_eval_model(),
        },
        "updated_by": saved.get("updated_by"),
        "updated_at": saved.get("updated_at"),
    }

@app.put("/api/admin/settings")
def put_settings(body: SettingsUpdate, request: Request):
    changes: Dict[str, Optional[str]] = {}
    if body.clear_gemini_api_key:
        changes["gemini_api_key"] = None
    elif body.gemini_api_key is not None:
        key = body.gemini_api_key.strip()
        if len(key) < 20 or any(c.isspace() for c in key):
            raise HTTPException(status_code=400, detail="This does not look like a Gemini API key")
        changes["gemini_api_key"] = key
    for field in ("chat_model", "eval_model"):
        value = getattr(body, field)
        if value is None:
            continue
        value = value.strip()
        if value and not MODEL_NAME.match(value):
            raise HTTPException(status_code=400, detail=f"Invalid model name: {value}")
        changes[field] = value or None
    if body.clear_ai_password:
        changes["ai_password"] = None
    elif body.ai_password is not None:
        password = body.ai_password.strip()
        if len(password) < 4:
            raise HTTPException(status_code=400, detail="AI password must be at least 4 characters")
        changes["ai_password"] = password
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change")
    settings.update(changes, current_user(request)["username"])
    return get_settings()

@app.post("/api/admin/settings/test-key")
def test_gemini_key(body: KeyTestRequest):
    """Send one tiny request to Gemini with the given key (or the current one)"""
    key = (body.gemini_api_key or "").strip() or settings.gemini_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="No API key to test")
    model = (body.model or "").strip() or settings.chat_model()
    if not MODEL_NAME.match(model):
        raise HTTPException(status_code=400, detail=f"Invalid model name: {model}")
    try:
        ChatGoogleGenerativeAI(api_key=key, model=model, max_retries=0, timeout=30).invoke("Reply with OK")
    except Exception as e:
        return {"ok": False, "model": model, "detail": str(e).split("\n")[0][:300]}
    return {"ok": True, "model": model}

# Models that cannot hold a text chat (speech, image, embedding and live audio models)
NON_CHAT_MODEL = re.compile(r"tts|image|embedding|native-audio|live|aqa|robotics|computer-use", re.I)

@app.get("/api/admin/models")
def list_models():
    """Gemini models the current API key can use for text chat"""
    key = settings.gemini_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="Set an API key first")
    # Key goes in a header, not the URL, so it stays out of request logs
    request = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                                     headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Gemini API answered {e.code}, check the API key")
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Cannot reach the Gemini API")
    models = []
    for m in data.get("models", []):
        name = m.get("name", "").split("/")[-1]
        if ("generateContent" in m.get("supportedGenerationMethods", [])
                and name.startswith("gemini") and not NON_CHAT_MODEL.search(name)):
            models.append({"name": name, "display_name": m.get("displayName") or name})
    models.sort(key=lambda m: m["name"], reverse=True)
    return {"models": models}

# ---------------------------------------------------------------------------
# Admin: chat history of evaluated cases
# ---------------------------------------------------------------------------
CASE_KEY = re.compile(r"^[\w\u0E00-\u0E7F-]{1,150}$")
SPEAKER = {"user": "Agent", "assistant": "Customer (bot)"}

def case_messages(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Messages of a case; older records only have the chat_history text"""
    if item.get("messages"):
        return [{"role": m.get("role"), "content": m.get("content", ""), "timestamp": m.get("timestamp")}
                for m in item["messages"] if m.get("role") in SPEAKER]
    messages: List[Dict[str, Any]] = []
    for line in (item.get("chat_history") or "").split("\n"):
        if line.startswith("**User** : "):
            messages.append({"role": "user", "content": line[len("**User** : "):], "timestamp": None})
        elif line.startswith("**Admin** : "):
            messages.append({"role": "assistant", "content": line[len("**Admin** : "):], "timestamp": None})
        elif messages and line.strip():
            messages[-1]["content"] += "\n" + line
    return messages

def average_score(item: Dict[str, Any]) -> float:
    values = [v for v in (item.get("scores") or {}).values() if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 2) if values else 0

def case_summary(key: str, item: Dict[str, Any]) -> Dict[str, Any]:
    config = item.get("config") or {}
    return {
        "key": key,
        "agent_name": item.get("agent_name") or "Unknown",
        "evaluation_date": item.get("evaluation_date", ""),
        "ai_enabled": bool(item.get("ai_enabled")),
        "average_score": average_score(item),
        "total_time_minutes": round(item.get("total_time_minutes") or 0, 2),
        "message_count": len(case_messages(item)),
        "domain": config.get("domain"),
        "job_position": config.get("job_position"),
        "difficulty": config.get("difficulty"),
    }

def iter_cases():
    for name, data in store.items("score/"):
        if isinstance(data, dict):
            yield name[len("score/"):-len(".json")], data

def load_case(key: str) -> Dict[str, Any]:
    data = store.get(f"score/{key}.json") if CASE_KEY.match(key) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Case not found")
    return data

def download_response(content: bytes, filename: str, media_type: str) -> Response:
    ascii_name = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})

def case_header_lines(key: str, item: Dict[str, Any]) -> List[str]:
    config = item.get("config") or {}
    lines = [
        f"Case: {key}",
        f"Agent: {item.get('agent_name') or 'Unknown'}",
        f"Evaluated: {item.get('evaluation_date', '')}",
        f"AI: {'Enabled' if item.get('ai_enabled') else 'Disabled'}",
        f"Duration: {item.get('total_time_minutes') or 0:.2f} minutes",
    ]
    for label, name in (("Domain", "domain"), ("Job position", "job_position"),
                        ("Difficulty", "difficulty"), ("Language", "language")):
        if config.get(name):
            lines.append(f"{label}: {config[name]}")
    scores = item.get("scores") or {}
    if scores:
        lines.append("Scores: " + ", ".join(f"{k.replace('_', ' ').title()} {v}/5" for k, v in scores.items())
                     + f" (average {average_score(item)})")
    if item.get("comment"):
        lines.append(f"Comment: {item['comment']}")
    return lines

def case_text(key: str, item: Dict[str, Any]) -> str:
    agent = item.get("agent_name") or "Agent"
    out = case_header_lines(key, item) + ["", "Conversation", "------------"]
    for m in case_messages(item):
        who = f"Agent ({agent})" if m["role"] == "user" else SPEAKER["assistant"]
        when = f"[{m['timestamp'][11:19]}] " if m.get("timestamp") else ""
        out.append(f"{when}{who}: {m['content']}")
    return "\n".join(out) + "\n"

def case_docx(key: str, item: Dict[str, Any]) -> bytes:
    agent = item.get("agent_name") or "Agent"
    doc = Document()
    doc.add_heading(f"Chat history: {agent}", 0)
    for line in case_header_lines(key, item):
        doc.add_paragraph(line)
    doc.add_heading("Conversation", level=1)
    for m in case_messages(item):
        p = doc.add_paragraph()
        who = f"Agent ({agent})" if m["role"] == "user" else SPEAKER["assistant"]
        when = f" {m['timestamp'][11:19]}" if m.get("timestamp") else ""
        p.add_run(f"{who}{when}: ").bold = True
        p.add_run(m["content"])
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

def excel_text(value: Any) -> Any:
    """Keep chat text from being read as an Excel formula, and within Excel's cell limit"""
    if isinstance(value, str):
        if value[:1] in ("=", "+", "-", "@"):
            value = "'" + value
        return value[:32000]
    return value

@app.get("/api/admin/cases")
def list_cases():
    cases = [case_summary(key, item) for key, item in iter_cases()]
    cases.sort(key=lambda c: c["evaluation_date"], reverse=True)
    return {"cases": cases}

@app.get("/api/admin/cases/{key}")
def get_case(key: str):
    item = load_case(key)
    return {**case_summary(key, item), "scores": item.get("scores") or {}, "comment": item.get("comment", ""),
            "config": item.get("config") or {}, "messages": case_messages(item)}

@app.get("/api/admin/cases/{key}/export")
def export_case(key: str, format: str = "docx"):
    item = load_case(key)
    if format == "txt":
        return download_response(case_text(key, item).encode("utf-8"), f"{key}.txt", "text/plain; charset=utf-8")
    if format == "docx":
        return download_response(case_docx(key, item), f"{key}.docx",
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    raise HTTPException(status_code=400, detail="format must be docx or txt")

class CasesExportRequest(BaseModel):
    keys: List[str]

@app.post("/api/admin/cases/export")
def export_cases(body: CasesExportRequest):
    """Excel file of the selected cases: one row per case, and one row per message"""
    if not body.keys:
        raise HTTPException(status_code=400, detail="No cases selected")
    case_rows, message_rows = [], []
    for key in body.keys:
        item = load_case(key)
        summary = case_summary(key, item)
        scores = item.get("scores") or {}
        case_rows.append({**summary, **scores, "comment": item.get("comment", ""),
                          "transcript": case_text(key, item)})
        for i, m in enumerate(case_messages(item), 1):
            message_rows.append({"key": key, "agent_name": summary["agent_name"], "no": i,
                                 "speaker": "Agent" if m["role"] == "user" else "Customer",
                                 "timestamp": m.get("timestamp") or "", "message": m["content"]})
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(case_rows).map(excel_text).to_excel(writer, sheet_name="Cases", index=False)
        pd.DataFrame(message_rows).map(excel_text).to_excel(writer, sheet_name="Messages", index=False)
    filename = f"chat_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return download_response(buffer.getvalue(), filename,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
