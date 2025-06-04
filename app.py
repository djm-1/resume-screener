import os
import streamlit as st
from multi_agents import *
from langgraph.graph import StateGraph, END
from PIL import Image

def load_image(image_file):
    return Image.open(image_file)

def main():
    st.set_page_config(layout="wide")
    st.title("Resume Screening and Matching Assistant")

    st.markdown("""
    <div style="background-color:#003366;padding:10px">
        <h2 style="color:white;text-align:center;">
            Resume Matching using LangGraph, RAG, and Llama3
        </h2>
    </div>
    """, unsafe_allow_html=True)

    # Upload resume PDF
    pdf_file = st.file_uploader("Upload Resume (PDF)", type=["pdf"])
    if pdf_file is not None:
        with open("Resume.pdf", "wb") as f:
            f.write(pdf_file.read())

    # Upload JD text or paste manually
    text_file = st.file_uploader("Upload Job Description (TXT)", type=["txt"])
    job_description = ""
    if text_file is not None:
        job_description = text_file.read().decode("utf-8", errors="ignore")
    else:
        job_description = st.text_area("Or paste the Job Description here:")

    # Save job description to file
    if job_description.strip() != "":
        with open("JD.txt", "w", encoding="utf-8") as f:
            f.write(job_description)

    # Start pipeline
    if st.button("Match Resume"):
        inputs = {
            "messages": ["You are a recruitment expert and your role is to match a candidate's profile with a given job description."]
        }

        # Define LangGraph workflow
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
        app = workflow.compile()

        # Optional: Show graph image
        img_data = app.get_graph().draw_mermaid_png()
        with open("workflow.png", "wb") as f:
            f.write(img_data)
        st.image(load_image("workflow.png"), caption="Agent Workflow")

        # Stream the workflow outputs
        outputs = app.stream(inputs)
        results = []
        for output in outputs:
            for key, value in output.items():
                messages = value.get("messages", [])
                for msg in messages:
                    results.append(f"**{key} Output:** {msg}")

        # Show results
        st.markdown("## 🔍 Results")
        for result in results:
            st.success(result)

if __name__ == "__main__":
    main()
