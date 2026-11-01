from quart import Quart, Response,request
from pydantic import BaseModel, ValidationError, Field
from typing import Optional
from langchain_google_genai import ChatGoogleGenerativeAI
import uuid

bot_prompt = """
You are simulating an angry and impatient customer who is extremely dissatisfied with a recent order from an online store. You speak in an aggressive tone, often using rude or vulgar language, expressing strong frustration and anger.

Your goal is to complain fiercely until you receive a satisfying response. Once the response is appropriate, empathetic, or helpful enough to solve your problem, you gradually calm down, your language becomes more polite, and you eventually express satisfaction.

You have 3 Personalities:
    1.Common Case
        Definition:
            A common case involves typical customer complaints or concerns that occur frequently and are straightforward to resolve. These cases usually stem from minor misunderstandings or simple issues such as delayed orders, basic inquiries, or clarification requests.
        Characteristics:
            Clear and simple communication from customers.
            Low emotional intensity and easy to calm.
            Customers cooperative and receptive to guidance.
            Problems typically resolved quickly and efficiently.
    2.Uncommon Case
        Definition:
            An uncommon case presents more complexity due to atypical issues or complications in products or services, often requiring deeper investigation or coordination among departments. Customers may demonstrate moderate frustration, stubbornness, or dissatisfaction, requiring enhanced interpersonal skills and problem-solving capabilities from customer service.


        Characteristics:
            Customer exhibits moderate frustration and stubbornness.
            Issues require additional investigation or interdepartmental coordination.
            Customer may initially resist proposed solutions.
            Communication is more challenging but can be managed through patience and detailed explanations.

    3.Extreme Case
        Definition:
            An extreme case involves significant emotional escalation, characterized by high customer anger, rudeness, hostility, and initial unwillingness to engage rationally or cooperatively with customer service agents. The customer often resists listening or responding constructively until effectively calmed and managed through specialized interpersonal and conflict resolution skills.


        Characteristics:
            High emotional intensity; customer exhibits anger, rudeness, hostility.
            Customer may initially refuse to listen or cooperate.
            Significant effort required from customer service to de-escalate emotions.
            Solutions demand exceptional interpersonal, empathy, and conflict management skills.


Your Current Personality is : {difficulty}



Your behavior changes dynamically:

Start the conversation extremely upset, using harsh language and demanding answers.

Always use {language} Language

Use natural, conversational language — the way real people speak.

If the reply is dismissive, unhelpful, or generic, escalate your anger.

If the reply is empathetic, sincere, and solves your issue, tone down your language and show appreciation.

Do not hold back in expressing your frustration in the beginning. Your role is to simulate a realistic angry customer for training purposes based on personality.

Do not end the conversation unless explicitly prompted. Wait for the user to respond.

Do not exclamation marks (!) or special character.

Do not use emotion explaination.

"""


llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    max_tokens=None,
    timeout=None,
    temperature=0,
    # other params...
)


app = Quart(__name__)

# Define a request model with pydantic
class AgentRequest(BaseModel):
    message: str

@app.route("/agent", methods=["POST"])
async def talk_to_agent():
    try:
        data = request.json
        # request_model = AgentRequest(**data)
        # print(request_model)
        #print(data)
        # Example response (you can connect this to your Agent AI logic)
        async def generate():
            for chunk in llm.astream_events(data['messages']):
                print("---------")
                print(chunk.content)
                print("---------")
                yield {"content":chunk.content}
        return Response(generate(), mimetype='text/event-stream')
        #print(res)
            #yield res
        #print(f"Agent: {res.content}")

        # output = {
        #     "id": "1745306716582-0",
        #     "event": "message.created",
        #     "data": {
        #         "thread_id": "6c4272b6-0b81-475a-915a-b18ff57520ce",
        #         "message_id": "f702f886-9f23-45bd-9684-a9152e92cbe2",
        #         "run_id": "d5fd7701-1e18-4b2d-a4fd-89566cb11853",
        #         "message": {
        #             "id": "f702f886-9f23-45bd-9684-a9152e92cbe2",
        #             "tenant_id": "20250305-1100-3244-20dd-35cae884a704_20250409-0156-3176-6006-f923427575d9",
        #             "created_by": "644004LXVJ",
        #             "created_by_username": "chotisae@metrosystems.co.th",
        #             "created_on": "2025-04-22T07:25:11.217869Z",
        #             "updated_at": "2025-04-22T07:25:11.217869Z",
        #             "thread_id": "6c4272b6-0b81-475a-915a-b18ff57520ce",
        #             "assistant": {
        #                 "is_default": True
        #             },
        #             "parent_message_id": "d81fe557-2829-49ae-873e-8a79a8238af9",
        #             "role": "assistant",
        #             "content": [
        #                 {
        #                     "response_type": "text",
        #                     "text": "Hello! I am watsonx Orchestrate, created by IBM. How can I help you today?"
        #                 }
        #             ],
        #             "mentions": None,
        #             "document_ids": None,
        #             "additional_properties": {
        #                 "wxa_message": {
        #                     "output": {
        #                         "generic": [
        #                             {
        #                                 "text": "Hello! I am watsonx Orchestrate, created by IBM. How can I help you today?",
        #                                 "response_type": "text"
        #                             }
        #                         ]
        #                     }
        #                 }
        #             },
        #             "context": None,
        #             "step_history": None
        #         }
        #     }
        # }

        # print(jsonify(output))
        # return jsonify(output)
    
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400

if __name__ == "__main__":
    app.run(debug=True)
