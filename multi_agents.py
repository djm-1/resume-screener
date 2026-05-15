import warnings
warnings.filterwarnings("ignore")

import operator
import os
from typing import Annotated, Sequence, TypedDict
from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI
from langchain_community.document_loaders import PyPDFLoader
from dotenv import load_dotenv

load_dotenv()

# Initialize LLM
llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)

# TypedDict for AgentState
class AgentState(TypedDict):
    messages: Annotated[Sequence[str], operator.add]
    resume_path: str
    job_description: str
    scoring_weights: dict



# ----------------- Resume Name Agent -----------------
def agent(agentState: AgentState):
    try:
        pdf_file = agentState["resume_path"]
        data = PyPDFLoader(pdf_file).load()
        resume_text = " ".join([page.page_content for page in data])
        response = llm.invoke(
            f"Your task is to extract candidate identity details from the resume data.\n"
            f"Return output in exactly this format:\n"
            f"Candidate Name: <name>\n"
            f"Contact Details: <email | phone | linkedin if available>\n"
            f"Resume Data: {resume_text}"
        )
        answer = response.content
    except Exception as ex:
        answer = f"Error extracting name: {ex}"
    return {"messages": [answer]}


# ----------------- Job Description Agent -----------------
def JD_agent(agentState: AgentState):
    try:
        jd_data = agentState["job_description"]
        response = llm.invoke(
            f"Your task is to extract the exact job requirements from the given data. "
            f"Only respond with the job requirements and nothing else. Data: {jd_data}"
        )
        result = response.content.replace("\n", "")
    except Exception as ex:
        result = f"Error extracting job description: {ex}"
    return {"messages": [result]}


# ----------------- Red Flag Detection Agent -----------------
def redflag_agent(agentState: AgentState):
    try:
        pdf_file = agentState["resume_path"]
        data = PyPDFLoader(pdf_file).load()
        resume_text = " ".join([page.page_content for page in data])

        prompt = f"""
        You are a Resume Screening Assistant.

        Your task is to analyze the candidate's resume and identify any potential **red flags** or **concerns** a recruiter might have.

        Look for the following:
        - Frequent job switching (e.g., jobs lasting <1 year repeatedly)
        - Unexplained employment gaps
        - Lack of relevant experience for technical claims
        - Missing education details
        - Irrelevant experience
        - Spelling or grammar issues

        Return a list of clear points like:
        - "Employment gap between 2020–2022"
        - "Mentions Python skills but no project or job experience using it"
        - "No education information found"

        Resume Data: {resume_text}
        """

        response = llm.invoke(prompt)
        result = response.content
    except Exception as ex:
        result = f"Error in redflag agent: {ex}"
    return {"messages": [result]}



# ----------------- Recruit Agent (Evaluation) -----------------
def recruit_agent(agentState: AgentState):
    try:
        pdf_file = agentState["resume_path"]
        data = PyPDFLoader(pdf_file).load()
        resume_text = " ".join([page.page_content for page in data])
        messages = agentState["messages"]
        derived_context = " ".join([str(m) for m in messages[-2:]]) if messages else ""
        jd_data = (
            f"{agentState['job_description']}\n\n"
            f"Additional extracted context from other agents:\n{derived_context}"
        )
        weights = agentState["scoring_weights"]

        skills_weight = int(weights.get("skills", 30))
        experience_weight = int(weights.get("experience", 50))
        education_weight = int(weights.get("education", 10))
        extras_weight = int(weights.get("extras", 10))

        prompt = f"""
        You are a Recruitment AI Assistant.

        Your task is to evaluate how well a candidate’s resume matches a given job description and assign a score out of 100 based on the criteria below.

        Scoring Criteria:
        - **Skills Match: {skills_weight} points**
        - **Experience Match: {experience_weight} points**
            - ⚠️ Do NOT award experience points for roles unrelated to the job description.
            - For freshers:
                - Evaluate based on relevant internships, academic projects, or personal/portfolio work that aligns with the job.
            - For experienced candidates:
                - 0–{round(experience_weight * 0.6)} pts: Award based on **years of relevant experience**.
                - 0–{experience_weight - round(experience_weight * 0.6)} pts: Award based on **quality, relevance, and impact** of work.
        - **Education Match: {education_weight} points**
            - ⚠️ If the education does NOT match the required fields (e.g., Computer Science, Data Science, AI, or related fields), assign **0 points**, regardless of degree level.
        - **Extras (Certifications, Awards, Side Projects): {extras_weight} points**

        Instructions:
        - Extract and compare the candidate’s **skills**, **experience**, **education**, and **additional qualifications** to the job description.
        - Apply the scoring rules strictly, especially for experience and education.
        - Do not award points for irrelevant experience.

        After evaluation, return:
        1. **Total score (out of 100)**
        2. **Score breakdown by category** (e.g., Skills: 24/{skills_weight}, Experience: 32/{experience_weight})
        3. **A short summary** (3–4 lines) covering major strengths and missing areas.
        4. **A final recommendation**, based on these rules:
            - If the candidate scores **above 75** and meets the key job requirements:
                - Say: **✅ I recommend this candidate for the job.**
            - If the candidate scores **between 50 and 75**, with partial matches in skills or experience:
                - Say: **❌ I do not recommend this candidate for this specific job.**
                - Follow with: **However, I recommend this candidate for an internship or entry-level position, as they show foundational potential.**
            - If the candidate scores **below 50**:
                - Say: **❌ I do not recommend this candidate for the job.**
                - Follow with a reason based on the biggest gaps (skills, experience, or education).

        5. End your response with: **TOTAL_SCORE: <numeric_value_out_of_100>**

        Resume Data:
        {resume_text}

        Job Description:
        {jd_data}
        """




        response = llm.invoke(prompt)
        answer = response.content
    except Exception as ex:
        answer = f"Error in recruit agent: {ex}"
    return {"messages": [answer]}


def build_workflow():
    workflow = StateGraph(AgentState)
    workflow.add_node("Resume_agent", agent)
    workflow.add_node("JD_agent", JD_agent)
    workflow.add_node("Redflag_agent", redflag_agent)
    workflow.add_node("Recruiter_agent", recruit_agent)

    workflow.set_entry_point("Resume_agent")
    workflow.add_edge("Resume_agent", "JD_agent")
    workflow.add_edge("Resume_agent", "Redflag_agent")
    workflow.add_edge("JD_agent", "Recruiter_agent")
    workflow.add_edge("Redflag_agent", "Recruiter_agent")
    workflow.add_edge("Recruiter_agent", END)

    return workflow.compile()
