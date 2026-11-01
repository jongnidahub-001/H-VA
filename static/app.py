from graph import Graph, SentimentAnalysis,AdminAssistant, EmotionAssistant
import asyncio
import streamlit as st
from langchain_core.messages import HumanMessage,SystemMessage,AIMessage
import uuid
import pandas as pd
from pydantic import BaseModel, Field
from typing import Literal,List
from langchain_google_genai import ChatGoogleGenerativeAI
from datetime import datetime
import json
import os
#from langchain.agents import create_react_agent


if 'sidebar_state' not in st.session_state:
    st.session_state.sidebar_state = 'expanded'
st.set_page_config(
    page_title="Chat with PDF",
    page_icon="./src/hi-km-logo.png",
    layout="wide",
    initial_sidebar_state=st.session_state.sidebar_state,

)

st.logo("./src/hi-km-logo.png", size="large",icon_image="./src/hi-km-logo.png")
st.html("""
  <style>
    [alt=Logo] {
      height: 3rem;
    }
  </style>
        """)

df = pd.read_csv('customer_complaints.csv')
df_domain = df.drop_duplicates(subset=['Domain'])
df_domain = df_domain['Domain'].values

graph = Graph()
sentiment = SentimentAnalysis()
assistant = AdminAssistant()
emotion = EmotionAssistant()

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

class Evaluation(BaseModel):
    response_accuracy : int = Field(
        description="Response Accuracy from 1 to 5"
    )
    response_time : int = Field(
        description="Response Time from 1 to 5"
    )
    empathy_and_tone : int = Field(
        description="Empathy and Tone from 1 to 5"
    )
    clarity : int = Field(
        description="Clarity from 1 to 5"
    )
    professionalism : int = Field(
        description="Professionalism from 1 to 5"
    )
    
    comment : str = Field(
        description="Comment for the evaluation"
    )

def evaluate_case_dialog(chat_history,total_time):
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-pro",
        max_tokens=None,
        timeout=30,
        temperature=0,
        max_retries=3,
    )
    model = llm.with_structured_output(Evaluation)
    prompt = """
        You are an expert Agent Performance Evaluator. Provide a rating from 1-5 for each of the following criteria based on the chat history and total resolution time provided.  Justify each rating with SPECIFIC examples from the chat history.

        1. Accuracy & Policy Adherence:
        Definition:  The correctness and completeness of information provided AND whether the agent correctly followed company policies.
        Rating (1-5):
            1/5: Information was completely incorrect or unrelated, AND the agent demonstrably violated company policy.
            Example: Agent gave the wrong return address AND offered a refund amount that was against company policy.
            2/5: Information was partially correct with significant errors/omissions OR the agent made a minor policy error.
            Example: Agent correctly identified the return policy timeframe but misstated the required return shipping method.
            3/5: Mostly correct information with minor inaccuracies OR a very slight deviation from best practices.
            Example: Agent correctly explained the warranty but forgot to mention the customer needed to retain proof of purchase.
            4/5: Fully correct information, addressing the core issue effectively AND followed all relevant policies.
            Example: Agent accurately explained the return process, provided the correct return address, and confirmed the customer was within the eligible return timeframe.
            5/5: Completely accurate, comprehensive information, perfectly resolved the customer's issue AND flawlessly adhered to all applicable company policies.
            Example:  Agent anticipated customer questions, proactively provided links to relevant FAQs, accurately processed the return request according to policy, and confirmed all details with the customer.

        2. Responsiveness (Speed & Efficiency):
        Definition:  The promptness of the interaction, considering BOTH first response time AND total resolution time, relative to expected standards.
        Rating (1-5):
            1/5:  Extremely delayed first response AND total resolution time far exceeded acceptable limits for the issue type.
            Example: First response > 10 minutes AND total resolution > 24 hours for a damaged goods claim (where the expected resolution is < 2 hours).
            2/5:  Noticeably delayed first response OR total resolution time exceeded expected limits.
            Example: First response > 5 minutes OR total resolution was 4 hours for a simple password reset (expected < 15 minutes).
            3/5:  Met basic acceptable response standards, but slower than ideal.
            Example: First response within 2 minutes, total resolution within the expected timeframe, but no proactive updates were provided to the customer.
            4/5:  Prompt first response AND total resolution within the expected timeframe.
            Example: First response < 1 minute AND total resolution within the target timeframe for the specific issue.
            5/5:  Exceptionally fast first response AND total resolution significantly exceeded target response times.  Proactive communication kept the customer informed.
            Example:  First response immediate, issue resolved in half the expected time, with regular updates to the customer throughout the process.

        3. Clarity & Completeness:
        Definition: Simplicity, comprehensibility, ease of understanding, AND completeness of the information provided. Did the agent provide all necessary information for the customer to take the next step?
        Rating (1-5):
            1/5:  Communication was confusing, unclear, difficult to understand, AND lacked essential information.
            Example: Agent used technical jargon without explanation AND failed to provide instructions on how to submit a claim.
            2/5:  Somewhat unclear or vague, leaving the customer confused OR missing key information.
            Example: Agent explained the return process but didn't specify *where* to ship the item.
            3/5:  Generally clear but included some ambiguous statements or unnecessary complexity OR missed a minor piece of information.
            Example: Agent explained the warranty but forgot to mention the customer needed to retain proof of purchase (although this was available in the website's FAQ).
            4/5:  Clear, concise, easy to understand, AND provided all essential information for the customer to proceed.
            Example: Agent explained the return process clearly, providing step-by-step instructions and a link to the return label.
            5/5: Exceptionally clear, straightforward, immediately understandable, AND anticipated potential customer questions by proactively providing additional helpful information.
            Example: Agent explained the return process in simple terms, provided a prepaid return label, included instructions on how to package the item securely, AND offered a discount code for a future purchase to compensate for the inconvenience.

        4. Empathy & Acknowledgment of Inconvenience:
        Definition: Demonstration of emotional understanding, genuine care, AND explicit acknowledgment of any inconvenience caused to the customer.
        Rating (1-5):
            1/5:  No empathy shown; responses felt dismissive or indifferent AND no acknowledgment of the customer's issue.
            Example:  Agent simply stated the policy without acknowledging the customer's frustration with a defective product.
            2/5:  Limited empathy, superficially acknowledged the issue without genuine concern.
            Example: "I understand your concern" without any further expression of empathy or apology.
            3/5:  Adequate empathy, showing general concern, but lacked deep emotional understanding or specific acknowledgment of inconvenience.
            Example: "I'm sorry for the problem" without further explanation or attempt to understand the customer's specific situation.
            4/5:  Strong empathy, clearly acknowledged the customer's feelings and concerns with genuine care AND specifically acknowledged the inconvenience.
            Example: "I understand how frustrating it must be to receive a damaged product, and I sincerely apologize for the inconvenience this has caused."
            5/5: Outstanding empathy, fully understood the customer's emotional state, showed exceptional sensitivity, AND went above and beyond to compensate for the inconvenience.
            Example:  "I understand how incredibly frustrating it is to receive a damaged product, especially after you were looking forward to it. I'm truly sorry for the inconvenience this has caused. To make up for this, I'm issuing a full refund AND sending you a replacement product free of charge. I've also added a small gift to your order as a token of our apology."

        5. Professionalism & Brand Representation:
        Definition: Courtesy, politeness, respect, appropriate conduct, AND positive representation of the organization's values and brand image.
        Rating (1-5):
            1/5:  Highly unprofessional; rude, disrespectful, inappropriate behavior, AND negatively impacted the company's image.
            Example: Agent used sarcastic language, argued with the customer, OR made disparaging remarks about the company.
            2/5:  Somewhat unprofessional; mild discourtesy or lack of politeness OR inconsistent with brand voice.
            Example: Agent used overly casual language or failed to address the customer by name.
            3/5:  Sufficiently professional, maintaining basic courtesy but without exceptional politeness or warmth AND neutral brand representation.
            Example: Agent was polite and helpful but didn't go the extra mile to create a positive experience.
            4/5:  Notably professional; consistently courteous, respectful, and appropriate throughout the interaction AND positively represented the company.
            Example: Agent was polite, helpful, and used professional language.
            5/5: Impeccably professional; demonstrated exceptional politeness, courtesy, respectfulness, appropriate conduct, AND consistently reinforced the company's values and brand image.
            Example: Agent was exceptionally polite, anticipated the customer's needs, and went above and beyond to provide a positive and memorable experience, reinforcing the company's commitment to customer satisfaction.

        Here is the chat history for the case:
        {chat_history}

        Here is the total time to resolve the case:
        {total_time}

    """.format(chat_history=chat_history,total_time=total_time)

    print(prompt)

    response = model.invoke(prompt)
    return response


@st.dialog("Test Dialog")
async def test_dialog(text):
    st.write(text)


@st.dialog("Evaluation Case")
def evaluate_case(agent_name,chat_history):
    chat_history_str = ""
    for msg in chat_history:
        if isinstance(msg, AIMessage):
            chat_history_str += f"**User** : {msg.content}\n"
        else:
            chat_history_str += f"**Admin** : {msg.content}\n"

    from datetime import datetime
    star = ":star:"
    st.write("Great Job! You successfully solved another case for today!")
    st.write("Customer Interaction Evaluation")
    st.divider()
    st.write(f"Agent Name : {agent_name}")
    st.write(f"Date/Time : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    now = datetime.now()
    elapsed_minutes = (now - st.session_state.start_time).total_seconds() / 60
    st.write(f"Total Time for solve the case : {elapsed_minutes:.2f} minutes")
    st.divider()
    response = evaluate_case_dialog(chat_history_str, f"Total Time : {elapsed_minutes:.2f} minutes")
    st.write(f"1. Response Accuracy : {star*response.response_accuracy}")
    st.write(f"2. Response Time : {star*response.response_time}")
    st.write(f"3. Empathy and Tone : {star*response.empathy_and_tone}")
    st.write(f"4. Clarity : {star*response.clarity}")
    st.write(f"5. Professionalism : {star*response.professionalism}")
    st.write(f"Comment : {response.comment}")

    if st.button("Save Result & Close"):
        # Create the log/score directory if it doesn't exist
        os.makedirs("log/score", exist_ok=True)
        
        # Generate unique ID and timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]  # Use first 8 characters of UUID
        filename = f"{agent_name}_{timestamp}_{unique_id}.json"
        filepath = os.path.join("log/score", filename)
        
        # Prepare the evaluation data
        evaluation_data = {
            "agent_name": agent_name,
            "timestamp": datetime.now().isoformat(),
            "evaluation_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "total_time_minutes": elapsed_minutes,
            "scores": {
                "response_accuracy": response.response_accuracy,
                "response_time": response.response_time,
                "empathy_and_tone": response.empathy_and_tone,
                "clarity": response.clarity,
                "professionalism": response.professionalism
            },
            "comment": response.comment,
            "chat_history": chat_history_str,
            "case_id": str(st.session_state.id)
        }
        
        # Save to JSON file
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(evaluation_data, f, indent=2, ensure_ascii=False)
            st.success(f"Evaluation saved successfully to {filename}")
            st.rerun()  # Close the dialog and refresh
        except Exception as e:
            st.error(f"Error saving evaluation: {str(e)}")


@st.fragment
def edit_btn(chat_history):
    if st.button("Close Case"):
        evaluate_case("Diff",chat_history)
    



async def main(task):
    
    if "memory" not in st.session_state:
        st.session_state.memory = ""

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "token" not in st.session_state:
        st.session_state.token = []
    
    if "sentiment" not in st.session_state:
        st.session_state.sentiment = 'neutral'
    

    
    
    

    workflow = graph.workflow(difficulty=st.session_state.difficulty,language=st.session_state.language)
    
    config = {'thread_id':st.session_state.id}
    with st.container():
        col1, col2, col3 = st.columns([1,2, 1])
        with col2.container(height=450):
            if "messages" in st.session_state:
                #st.write(st.session_state.messages)
                for msg in st.session_state.messages:
                    
                    # if len(st.session_state.messages) < 3:
                    #     continue
                    if msg.content == 'start':
                        continue
                    if isinstance(msg, HumanMessage):
                        with st.chat_message('👨‍💻'):
                            st.write('**user**')
                            st.write(msg.content)
                    else:   
                        with st.chat_message('👺'):
                            st.write(f'**{msg.name}**')
                            st.write(msg.content)

            if task != 'start':
                with st.chat_message('👨‍💻'):
                    st.write('**user**')
                    st.write(task)

            st.session_state.messages.append(HumanMessage(content=task))
            
            async for m in workflow.astream({"messages": [SystemMessage(content='Here is the domain using for generate situation : ' + st.session_state.domain),SystemMessage(content='Here is the character of conversation : ' + st.session_state.difficult)]+st.session_state.messages}, config,stream_mode='updates'):
                for key,value in m.items():
                    print(f"{key} : {value}")

                    with st.chat_message('👺'):
                        st.write(f'**{key}**')
                        st.write(value["messages"][-1].content)

                    st.session_state.messages.append(AIMessage(content=value['messages'][-1].content,name=sanitize_name(key)))
                    
                    # Store the latest message for use outside the loop
                    st.session_state.latest_message = value["messages"][-1].content
                    st.divider()
                    
                    
                    # sentiment_output = st.sidebar.select_slider(
                    #     "Last Message Sentiment",
                    #     options=[
                    #         "positive",
                    #         "neutral",
                    #         "negative",
                    #     ],
                    #     value=(sentiment.get_sentiment(value["messages"][-1].content))
                    # )
                    # print(sentiment.get_sentiment(value["messages"][-1].content))
                    # sentiment_output = st.sidebar.slider("Negative Sentiment Score", 0.0, 100.0, value=float(sentiment.get_sentiment(value["messages"][-1].content)))
                    # st.sidebar.write("Negative Sentiment Score :", sentiment_output)
                    # st.sidebar.divider()
                    #st.session_state.sentiment = sentiment.get_sentiment(value["messages"][-1].content)

                    # Generate and store admin suggestions
                    if st.session_state.ai_enabled:
                        option_map = assistant.get_suggest(st.session_state.messages)
                        st.session_state.suggestions = [i.content for i in option_map]
                    else:
                        st.session_state.suggestions = []
                    # for num, content in enumerate(option_map):
                    #     st.button(content.content,disabled=True)
                    # for i in option_map:
                    #     st.session_state.suggest.append(i.content)
                    #     st.sidebar.button(i.content)
                    #expander = st.expander("See Suggest Message")

                    #st.sidebar.button(emotion_output,disabled=True)
                    #st.sidebar.write(emotion_output)
                    st.divider()
        with col3.container(height=450):
            if st.session_state.ai_enabled and hasattr(st.session_state, 'latest_message'):
                sentiment_score = float(sentiment.get_sentiment(st.session_state.latest_message))
                sentiment_output = st.slider("**Negative Sentiment Score**", 0.0, 100.0, value=sentiment_score)
                st.write("Negative Sentiment Score :", sentiment_output)
                
                st.divider()
                if int(sentiment_score) < 20:
                    edit_btn(st.session_state.messages)
                    # if st.button("Close Case"):
                    #     evaluate_case("Diff")
                        #st.rerun()
        
        with col1.container(height=450):
            if st.session_state.ai_enabled and hasattr(st.session_state, 'latest_message'):
                st.write(f"**Emotion Reform : {st.session_state.reform}**")
                emotion_output = emotion.get_suggest(st.session_state.latest_message, st.session_state.reform, st.session_state.language)
                with st.expander("Click here to expand", expanded=True):
                    st.write(emotion_output)
                

                

                #sentiment_output = st.sidebar.slider("Last Message Sentiment", 'Positive', "Neutral", 'Negative',value=sentiment.get_sentiment(value["messages"][-1].content))
                #print(value["messages"][-1].pretty_print())



if __name__ == "__main__":

    if 'id' not in st.session_state:
        st.session_state.id = uuid.uuid4()

    if 'start' not in st.session_state:
        st.session_state.start = False
    
    if 'render' not in st.session_state:
        st.session_state.render = False

    if "ai_enabled" not in st.session_state:    
        st.session_state.ai_enabled = False

    if "latest_message" not in st.session_state:
        st.session_state.latest_message = ""

    if "suggestions" not in st.session_state:
        st.session_state.suggestions = []
    
    if "selected_suggestion" not in st.session_state:
        st.session_state.selected_suggestion = ""


    def change():
        st.session_state.sidebar_state = (
            "collapsed" if st.session_state.sidebar_state == "expanded" else "expanded"
        )
        print(st.session_state.sidebar_state)

    def select_suggestion(suggestion_text):
        """Callback function to set selected suggestion"""
        st.session_state.selected_suggestion = suggestion_text

    
    col1, col2 = st.columns([3,1])
    with st.container():
        with col1.container():
            st.markdown("## Nida Bot : Customer Complaint Chatbot")
            #password = st.text_input("Enter a password",type="password")
        with col2.container():
            if not st.session_state.ai_enabled:
                enable_status = "OFF"
            else:
                enable_status = "ON"
            with st.expander(f"Enable AI : {enable_status}"):
                password = st.text_input("Enter a password",type="password")
                if password and password == os.getenv("AI_PASSWORD"):
                    if st.toggle("AI Enabled",value=True):
                        st.session_state.ai_enabled = True
                    else:
                        st.session_state.ai_enabled = False 
                if password and password == os.getenv("ADMIN_PASSWORD"):
                    if st.button("Download Score For All Agent"):
                        # Folder containing JSON files
                        folder_path = "./log/score"
                        output_file = "./log/score/all_agent_score.json"

                        merged_data = []

                        # Loop through all files in the folder
                        for filename in os.listdir(folder_path):
                            if filename.endswith(".json"):
                                file_path = os.path.join(folder_path, filename)
                                with open(file_path, "r", encoding="utf-8") as f:
                                    try:
                                        data = json.load(f)
                                        if isinstance(data, list):
                                            merged_data.extend(data)  # Merge lists
                                        else:
                                            merged_data.append(data)  # Merge single JSON objects
                                    except json.JSONDecodeError as e:
                                        print(f"Error reading {filename}: {e}")

                        print(merged_data)

                        bytes_data = json.dumps(merged_data,ensure_ascii=False,indent=4)
                        # Save the merged JSON
                        # with open(output_file, "w", encoding="utf-8") as f:
                        #     json.dump(merged_data, f, ensure_ascii=False, indent=4)

                        # print(f"Merged JSON saved to {output_file}")

                        st.download_button(
                            label="Download All Score",
                            data=bytes_data,  
                            file_name="all_agent_score.json",
                            mime="application/json",
                            icon=":material/download:",
                        )

                else:
                    st.button("Enable AI")

    
    with st.sidebar.expander("Chatbot Configuration",expanded=True):
        bot_option = st.selectbox(
            "**Select Language**",
            ("Thai", "English"),
        )
        st.session_state.language = bot_option
        option = st.selectbox(
            "**Complaint Domain**",
            (df_domain),
        )
        if option:
            sub_option = st.selectbox(
                "**Job Position**",
                (df[df['Domain'] == option]['Job Position'].values),
            )
            data = df[(df['Domain'] == option) & (df['Job Position'] == sub_option)]
            domain = f"**Complaint Detail** :  \n  **Domain** : {data['Domain'].values[0]}  \n  **Job Position** : {data['Job Position'].values[0]}  \n  **Category** : {data['Category'].values[0]}  \n  **Complaint** : {data['Complaint'].values[0]}"
            with st.popover("View Complaint Detail"):
                st.write(domain)
            diff_option = st.selectbox(
                "**Select Difficulty**",
                ("Common Case","Uncommon Case","Extreme Case"),
            )
            if diff_option == "Common Case":
                st.session_state.difficult = common_case_prompt
            elif diff_option == "Uncommon Case":
                st.session_state.difficult = uncommon_case_prompt
            elif diff_option == "Extreme Case":
                st.session_state.difficult = extreme_case_prompt

            st.session_state.difficulty = diff_option
            st.session_state.domain = domain

            reform_option = st.selectbox(
                "**Emotional Reform Style**",
                ("Assertive Leadership","Passive Leadership","Neutral Leadership"),
            )

            st.session_state.reform = reform_option


    if st.session_state.start == False:
        if st.sidebar.button(f"Generate : {data['Domain'].values[0]} Bot",on_click=change):
            st.session_state.start_time = datetime.now()
            st.session_state.start = True
            start = 'start'
            asyncio.run(main(start))
    else:
        #st.session_state.start = False
        if st.sidebar.button("Restart"):
            st.rerun()
    st.sidebar.divider()
    

    # Add this code at the beginning of your script or where you initialize your Streamlit app

    # if 'sentiment' in st.session_state:
    #     sentiment_output = st.sidebar.select_slider(
    #         "Last Message Sentiment",
    #         options=[
    #             "positive",
    #             "neutral",
    #             "negative",
    #         ],
    #         value=(st.session_state.sentiment)
    #     )
    #     st.sidebar.write("Last Message Sentiment :", sentiment_output)
    #     st.sidebar.divider()
    
    # if 'suggest' in st.session_state:
    #     for i in st.session_state.suggest:
    #         st.sidebar.button(i, on_click=asyncio.run(main(i)))

    # if "messages" in st.session_state:
    #     #st.write(st.session_state.messages)
    #     with st.container():
    #         col1, col2, col3 = st.columns([1,2, 1])
    #         with col2.container(height=450):
    #             if "messages" in st.session_state:
    #                 #st.write(st.session_state.messages)
    #                 for msg in st.session_state.messages:
                        
    #                     # if len(st.session_state.messages) < 3:
    #                     #     continue
    #                     if msg.content == 'start':
    #                         continue
    #                     if isinstance(msg, HumanMessage):
    #                         with st.chat_message('👨‍💻'):
    #                             st.write('**user**')
    #                             st.write(msg.content)
    #                     else:   
    #                         with st.chat_message('👺'):
    #                             st.write(f'**{msg.name}**')
    #                             st.write(msg.content)

    
   
    # if st.session_state.render:
    #     if "messages" in st.session_state:
    #         st.write(st.session_state.messages)
    #         for msg in st.session_state.messages:
                
    #             # if len(st.session_state.messages) < 3:
    #             #     continue
    #             # if msg.content == 'start':
    #             #     continue
    #             if isinstance(msg, HumanMessage):
    #                 with st.chat_message('👨‍💻'):
    #                     st.write('**user**')
    #                     st.write(msg.content)
    #             else:   
    #                 with st.chat_message('👺'):
    #                     st.write(f'**{msg.name}**')
    #                     st.write(msg.content)

    if st.session_state.start: 
        # Display admin suggestions above the chat input
        if st.session_state.ai_enabled and st.session_state.suggestions:
            st.write("**💡 Admin Suggestions:**")
            cols = st.columns(len(st.session_state.suggestions))
            for idx, suggestion in enumerate(st.session_state.suggestions):
                with cols[idx]:
                    if st.button(f"📝 {suggestion}", key=f"suggestion_{idx}", help="Click to use this suggestion"):
                        st.session_state.selected_suggestion = suggestion
                        st.rerun()
        
        # Chat input with pre-filled suggestion
        col_input, col_send = st.columns([4, 1])
        
        with col_input:
            # Use text_input that can accept default values
            task = st.text_input(
                "Ask a question",
                value=st.session_state.selected_suggestion,
                key="chat_input",
                label_visibility="collapsed",
                placeholder="Type your message here..."
            )
        
        with col_send:
            send_button = st.button("Send", type="primary", use_container_width=True)
        
        # Handle submission
        if send_button and task.strip():
            # Clear the selected suggestion after use
            st.session_state.selected_suggestion = ""
            asyncio.run(main(task))
            st.session_state.render = True
            st.rerun()
    
