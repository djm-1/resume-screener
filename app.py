import re
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from multi_agents import build_workflow

APP_BUILD_MARKER = "build-2026-05-17-fallback-sync"

try:
    import streamlit_agraph as st_agraph
    from streamlit_agraph import Node, Edge, Config

    AGRAPH_COMPONENT = getattr(st_agraph, "_agraph", None)
    HAS_AGRAPH = AGRAPH_COMPONENT is not None
except Exception:
    AGRAPH_COMPONENT = None
    HAS_AGRAPH = False


def normalize_weights(skills: int, experience: int, education: int, extras: int):
    raw_weights = {
        "skills": skills,
        "experience": experience,
        "education": education,
        "extras": extras,
    }
    total = sum(raw_weights.values())
    if total == 0:
        return None

    scaled = {k: (v / total) * 100 for k, v in raw_weights.items()}
    floored = {k: int(v) for k, v in scaled.items()}
    remainder = 100 - sum(floored.values())

    for key, _ in sorted(
        scaled.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True
    )[:remainder]:
        floored[key] += 1

    return floored


def parse_candidate_name(text: str, fallback: str):
    match = re.search(r"Candidate\s*Name\s*:\s*(.+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else fallback


def parse_total_score(text: str):
    score_match = re.search(r"TOTAL_SCORE\s*:\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if score_match:
        return float(score_match.group(1))

    generic_match = re.search(r"(?:Total\s*score|Score)\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    return float(generic_match.group(1)) if generic_match else None


def _clean_text(value: str):
    return re.sub(r"\*+", "", value or "").strip()


def parse_score_breakdown(text: str, fallback_weights: dict):
    labels = {
        "skills": "Skills",
        "experience": "Experience",
        "education": "Education",
        "extras": "Extras",
    }

    breakdown = {}
    for key, label in labels.items():
        pattern = rf"{label}\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            breakdown[key] = {
                "score": float(match.group(1)),
                "max": float(match.group(2)),
            }
        else:
            breakdown[key] = {
                "score": 0.0,
                "max": float(fallback_weights.get(key, 0)),
            }
    return breakdown


def parse_summary(text: str):
    summary_match = re.search(
        r"Summary\s*:\s*(.+?)(?:\n\s*Recommendation\s*:|\n\s*TOTAL_SCORE\s*:|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if summary_match:
        return _clean_text(summary_match.group(1))
    return "Summary not available."


def parse_recommendation(text: str):
    recommendation_match = re.search(
        r"Recommendation\s*:\s*(.+?)(?:\n\s*TOTAL_SCORE\s*:|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if recommendation_match:
        return _clean_text(recommendation_match.group(1))

    lines = [line.strip() for line in text.splitlines()]
    for line in lines:
        if "recommend" in line.lower():
            return _clean_text(line)
    return "Recommendation not available."


def parse_redflags(text: str):
    if not text:
        return []

    redflags = []
    for line in text.splitlines():
        stripped = line.strip().strip('"').strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "potential red flags" in lower or lower.startswith("here are"):
            continue
        if stripped.startswith("-"):
            stripped = stripped[1:].strip()
        if stripped:
            redflags.append(_clean_text(stripped.strip('"')))
    return redflags


def shorten_text(text: str, max_chars: int = 120):
    cleaned = re.sub(r"\s+", " ", _clean_text(text))
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3] + "..."


def _dot_escape(text: str):
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("|", "/")
    )


def build_agent_flow_agraph(status_map: dict, edge_labels: dict, current_agent: str = ""):
    node_names = {
        "Resume_agent": "Resume Agent",
        "JD_agent": "JD Agent",
        "Redflag_agent": "Redflag Agent",
        "Recruiter_agent": "Recruiter Agent",
    }

    status_palette = {
        "pending": ("PENDING", "#f8f9fa", "#adb5bd"),
        "running": ("RUNNING", "#e7f5ff", "#1971c2"),
        "done": ("DONE", "#ebfbee", "#2b8a3e"),
    }

    nodes = []
    for key in ["Resume_agent", "JD_agent", "Redflag_agent", "Recruiter_agent"]:
        status_key = status_map.get(key, "pending")
        status_text, fill, border = status_palette[status_key]
        if key == current_agent:
            status_text = f"{status_text} • ACTIVE"

        nodes.append(
            Node(
                id=key,
                label=node_names[key],
                title=f"{node_names[key]}\nStatus: {status_text}",
                shape="box",
                size=34,
                color={
                    "background": fill,
                    "border": border,
                    "highlight": {"background": "#fff3bf", "border": "#f08c00"},
                },
                borderWidth=4 if key == current_agent else 2,
                shadow=True,
                font={"size": 16, "face": "Inter", "color": "#212529"},
            )
        )

    edges = []
    edge_map = [
        ("Resume_agent", "JD_agent"),
        ("Resume_agent", "Redflag_agent"),
        ("JD_agent", "Recruiter_agent"),
        ("Redflag_agent", "Recruiter_agent"),
    ]
    for source, target in edge_map:
        edge_text = edge_labels.get((source, target), "")
        label = shorten_text(edge_text, 35) if edge_text else ""
        source_status = status_map.get(source, "pending")
        edge_color = "#2b8a3e" if source_status == "done" else "#868e96"
        edge_width = 3 if source_status == "done" else 2
        edges.append(
            Edge(
                source=source,
                target=target,
                label=label,
                title=edge_text or "Waiting for data...",
                color=edge_color,
                width=edge_width,
                arrows="to",
                smooth={"enabled": True, "type": "curvedCW", "roundness": 0.12},
                font={"size": 10, "align": "middle", "strokeWidth": 0},
            )
        )

    config = Config(
        width="100%",
        height=430,
        directed=True,
        physics=False,
        hierarchical=True,
        levelSeparation=220,
        nodeSpacing=190,
        treeSpacing=230,
        sortMethod="directed",
        nodeHighlightBehavior=True,
        highlightColor="#ffd43b",
        collapsible=False,
    )

    return nodes, edges, config


def render_agraph_with_key(nodes, edges, config, render_key: str):
    if not HAS_AGRAPH:
        return None

    node_ids = [node.id for node in nodes]
    if len(node_ids) > len(set(node_ids)):
        st.warning("Duplicated node IDs exist.")

    nodes_data = [node.to_dict() for node in nodes]
    edges_data = [edge.to_dict() for edge in edges]
    config_json = json.dumps(config.__dict__)
    data_json = json.dumps({"nodes": nodes_data, "edges": edges_data})

    return AGRAPH_COMPONENT(data=data_json, config=config_json, key=render_key)


def render_agent_flow_graph(
    graph_placeholder,
    status_map: dict,
    edge_labels: dict,
    current_agent: str = "",
    render_key: str = "agent_graph_default",
):
    if HAS_AGRAPH:
        nodes, edges, config = build_agent_flow_agraph(status_map, edge_labels, current_agent)
        with graph_placeholder.container():
            render_agraph_with_key(nodes, edges, config, render_key)
    else:
        graph_placeholder.graphviz_chart(build_agent_flow_dot(status_map, edge_labels, current_agent))


def build_agent_flow_dot(status_map: dict, edge_labels: dict, current_agent: str = ""):
    node_names = {
        "Resume_agent": "Resume Agent",
        "JD_agent": "JD Agent",
        "Redflag_agent": "Redflag Agent",
        "Recruiter_agent": "Recruiter Agent",
    }

    status_palette = {
        "pending": ("PENDING", "#f1f3f5", "#adb5bd"),
        "running": ("RUNNING", "#d0ebff", "#1c7ed6"),
        "done": ("DONE", "#d3f9d8", "#2b8a3e"),
    }

    lines = [
        "digraph AgentFlow {",
        "rankdir=LR;",
        'labelloc="t";',
        'label="Realtime Multi-Agent Flow";',
        'fontname="Helvetica";',
        'fontsize=16;',
        'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, penwidth=2];',
        'edge [fontname="Helvetica", fontsize=10, color="#495057", arrowsize=0.8];',
    ]

    for key in ["Resume_agent", "JD_agent", "Redflag_agent", "Recruiter_agent"]:
        status_key = status_map.get(key, "pending")
        status_text, fill, border = status_palette[status_key]
        if key == current_agent:
            status_text = f"{status_text} • ACTIVE"
        label = _dot_escape(f"{node_names[key]}\\n{status_text}")
        lines.append(
            f'"{key}" [label="{label}", fillcolor="{fill}", color="{border}"];'
        )

    edges = [
        ("Resume_agent", "JD_agent"),
        ("Resume_agent", "Redflag_agent"),
        ("JD_agent", "Recruiter_agent"),
        ("Redflag_agent", "Recruiter_agent"),
    ]

    for source, target in edges:
        label = _dot_escape(shorten_text(edge_labels.get((source, target), ""), 70))
        if label:
            lines.append(f'"{source}" -> "{target}" [label="{label}"];')
        else:
            lines.append(f'"{source}" -> "{target}";')

    lines.append("}")
    return "\n".join(lines)

def main():
    st.set_page_config(layout="wide")
    st.title("Resume Screening and Matching Assistant")

    st.markdown("""
    <div style="background-color:#003366;padding:10px">
        <h2 style="color:white;text-align:center;">
            Resume Matching using LangGraph and OpenAI
        </h2>
    </div>
    """, unsafe_allow_html=True)

    st.caption("Upload multiple resumes, score them against a job description, and compare candidates visually.")
    st.caption(f"Build marker: `{APP_BUILD_MARKER}`")

    st.subheader("Scoring Weights")
    col1, col2 = st.columns(2)
    with col1:
        skills_weight = st.slider("Skills Weight", 0, 100, 30)
        experience_weight = st.slider("Experience Weight", 0, 100, 50)
    with col2:
        education_weight = st.slider("Education Weight", 0, 100, 10)
        extras_weight = st.slider("Extras Weight", 0, 100, 10)

    normalized_weights = normalize_weights(
        skills_weight, experience_weight, education_weight, extras_weight
    )

    if normalized_weights is None:
        st.error("Please set at least one scoring weight greater than 0.")
    elif (skills_weight + experience_weight + education_weight + extras_weight) != 100:
        st.info(
            "Weights do not sum to 100, so they will be normalized automatically. "
            f"Using: Skills {normalized_weights['skills']}, "
            f"Experience {normalized_weights['experience']}, "
            f"Education {normalized_weights['education']}, "
            f"Extras {normalized_weights['extras']}"
        )

    # Upload resume PDFs (batch)
    pdf_files = st.file_uploader(
        "Upload Resumes (PDF)", type=["pdf"], accept_multiple_files=True
    )

    # Upload JD text or paste manually
    text_file = st.file_uploader("Upload Job Description (TXT)", type=["txt"])
    job_description = ""
    if text_file is not None:
        job_description = text_file.read().decode("utf-8", errors="ignore")
    else:
        job_description = st.text_area("Or paste the Job Description here:")

    if "candidate_results" not in st.session_state:
        st.session_state["candidate_results"] = []

    # Start pipeline
    if st.button("Match Resumes"):
        if normalized_weights is None:
            st.stop()

        if not pdf_files:
            st.error("Please upload at least one resume PDF.")
            st.stop()

        if not job_description.strip():
            st.error("Please provide a job description (upload TXT or paste text).")
            st.stop()

        app = build_workflow()

        tmp_dir = Path("uploaded_resumes")
        tmp_dir.mkdir(exist_ok=True)

        candidate_results = []
        progress = st.progress(0, text="Processing resumes...")

        st.markdown("## 🕸️ Realtime Agent Graph")
        realtime_left, realtime_right = st.columns([2, 1])
        with realtime_left:
            live_graph_placeholder = st.empty()
            handoff_placeholder = st.empty()
        with realtime_right:
            live_status_placeholder = st.empty()
            event_placeholder = st.empty()

        for index, pdf_file in enumerate(pdf_files, start=1):
            resume_path = tmp_dir / f"resume_{index}_{pdf_file.name}"
            with open(resume_path, "wb") as f:
                f.write(pdf_file.getbuffer())

            status_map = {
                "Resume_agent": "pending",
                "JD_agent": "pending",
                "Redflag_agent": "pending",
                "Recruiter_agent": "pending",
            }
            edge_labels = {
                ("Resume_agent", "JD_agent"): "",
                ("Resume_agent", "Redflag_agent"): "",
                ("JD_agent", "Recruiter_agent"): "",
                ("Redflag_agent", "Recruiter_agent"): "",
            }
            live_events = []

            live_status_placeholder.info(
                f"Processing resume {index}/{len(pdf_files)}: **{pdf_file.name}**"
            )
            if HAS_AGRAPH:
                live_status_placeholder.success(
                    f"Interactive graph enabled for {pdf_file.name}"
                )
            else:
                live_status_placeholder.warning(
                    f"Using Graphviz fallback for {pdf_file.name}"
                )

            graph_update_counter = 0
            render_agent_flow_graph(
                live_graph_placeholder,
                status_map,
                edge_labels,
                render_key=f"agent_graph_{index}_{graph_update_counter}",
            )
            graph_update_counter += 1
            event_placeholder.markdown("### Live Events\n- Waiting for first agent output...")

            inputs = {
                "messages": [
                    "You are a recruitment expert and your role is to match a candidate's profile with a given job description."
                ],
                "resume_path": str(resume_path),
                "job_description": job_description,
                "scoring_weights": normalized_weights,
            }

            outputs = app.stream(inputs)
            agent_outputs = {
                "Resume_agent": "",
                "JD_agent": "",
                "Redflag_agent": "",
                "Recruiter_agent": "",
            }

            for output in outputs:
                for key, value in output.items():
                    if key in status_map:
                        status_map[key] = "running"
                        render_agent_flow_graph(
                            live_graph_placeholder,
                            status_map,
                            edge_labels,
                            current_agent=key,
                            render_key=f"agent_graph_{index}_{graph_update_counter}",
                        )
                        graph_update_counter += 1

                    messages = value.get("messages", [])
                    text = "\n".join(str(msg) for msg in messages).strip()
                    if key in agent_outputs and text:
                        agent_outputs[key] = text

                        snippet = shorten_text(text, 110)
                        if key == "Resume_agent":
                            edge_labels[("Resume_agent", "JD_agent")] = snippet
                            edge_labels[("Resume_agent", "Redflag_agent")] = snippet
                        elif key == "JD_agent":
                            edge_labels[("JD_agent", "Recruiter_agent")] = snippet
                        elif key == "Redflag_agent":
                            edge_labels[("Redflag_agent", "Recruiter_agent")] = snippet

                        live_events.append(f"- **{key}** generated: {snippet}")
                        if len(live_events) > 10:
                            live_events = live_events[-10:]

                        handoff_rows = [
                            {
                                "From": src,
                                "To": tgt,
                                "Data Passed": shorten_text(lbl, 90) if lbl else "Waiting...",
                            }
                            for (src, tgt), lbl in edge_labels.items()
                        ]
                        handoff_df = pd.DataFrame(handoff_rows)
                        with handoff_placeholder.container():
                            st.markdown("### 🔄 Data Handoff")
                            st.dataframe(handoff_df, width="stretch")

                        event_placeholder.markdown(
                            "### Live Events\n" + "\n".join(live_events)
                        )

                    if key in status_map:
                        status_map[key] = "done"
                        render_agent_flow_graph(
                            live_graph_placeholder,
                            status_map,
                            edge_labels,
                            render_key=f"agent_graph_{index}_{graph_update_counter}",
                        )
                        graph_update_counter += 1

            candidate_name = parse_candidate_name(
                agent_outputs["Resume_agent"], fallback=pdf_file.name.rsplit(".", 1)[0]
            )
            total_score = parse_total_score(agent_outputs["Recruiter_agent"])
            breakdown = parse_score_breakdown(agent_outputs["Recruiter_agent"], normalized_weights)
            summary = parse_summary(agent_outputs["Recruiter_agent"])
            recommendation = parse_recommendation(agent_outputs["Recruiter_agent"])
            redflags = parse_redflags(agent_outputs["Redflag_agent"])

            candidate_results.append(
                {
                    "resume_file": pdf_file.name,
                    "candidate": candidate_name,
                    "total_score": total_score,
                    "breakdown": breakdown,
                    "summary": summary,
                    "recommendation": recommendation,
                    "redflags": redflags,
                    "agent_outputs": agent_outputs,
                    "flow_events": live_events,
                }
            )

            progress.progress(
                int((index / len(pdf_files)) * 100),
                text=f"Processed {index}/{len(pdf_files)} resumes",
            )

        progress.empty()

        st.session_state["candidate_results"] = candidate_results

    candidate_results = st.session_state.get("candidate_results", [])

    if candidate_results:
        ranked_rows = [
            {
                "Candidate": item["candidate"],
                "Resume File": item["resume_file"],
                "Total Score (/100)": item["total_score"],
                "Skills": item["breakdown"]["skills"]["score"],
                "Experience": item["breakdown"]["experience"]["score"],
                "Education": item["breakdown"]["education"]["score"],
                "Extras": item["breakdown"]["extras"]["score"],
            }
            for item in candidate_results
        ]

        ranked_df = pd.DataFrame(ranked_rows)
        ranked_df = ranked_df.sort_values(by="Total Score (/100)", ascending=False, na_position="last")

        st.markdown("## 📊 Candidate Dashboard")

        valid_scores = ranked_df["Total Score (/100)"].dropna()
        top_candidate = ranked_df.iloc[0]["Candidate"] if not ranked_df.empty else "N/A"

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Resumes Processed", len(candidate_results))
        kpi2.metric("Average Score", f"{valid_scores.mean():.1f}" if not valid_scores.empty else "N/A")
        kpi3.metric("Top Candidate", top_candidate)

        st.markdown("### 🏆 Ranked Candidates")
        st.dataframe(ranked_df, width="stretch")

        score_chart_df = ranked_df[["Candidate", "Total Score (/100)"]].dropna().set_index("Candidate")
        if not score_chart_df.empty:
            st.markdown("### 📈 Score Comparison")
            st.bar_chart(score_chart_df)

        category_chart_df = ranked_df[["Candidate", "Skills", "Experience", "Education", "Extras"]]
        category_chart_df = category_chart_df.set_index("Candidate")
        st.markdown("### 📚 Category-wise Breakdown")
        st.bar_chart(category_chart_df)

        st.markdown("## 👤 Candidate Insights")
        insight_tabs = st.tabs([item["candidate"] for item in candidate_results])

        for tab, item in zip(insight_tabs, candidate_results):
            with tab:
                insight_left, insight_right = st.columns([2, 1])

                with insight_left:
                    st.subheader(item["candidate"])
                    st.caption(f"Resume file: {item['resume_file']}")
                    score_value = item["total_score"]
                    st.metric("Total Score", f"{score_value:.1f}/100" if score_value is not None else "N/A")

                    st.markdown("**AI Summary**")
                    st.write(item["summary"])

                    st.markdown("**Recommendation**")
                    recommendation_text = item["recommendation"]
                    if "✅" in recommendation_text:
                        st.success(recommendation_text)
                    elif "❌" in recommendation_text:
                        st.warning(recommendation_text)
                    else:
                        st.info(recommendation_text)

                    st.markdown("**Red Flags**")
                    if item["redflags"]:
                        for flag in item["redflags"]:
                            st.write(f"- {flag}")
                    else:
                        st.write("No major red flags found.")

                with insight_right:
                    st.markdown("**Score Progress by Category**")
                    label_map = {
                        "skills": "Skills",
                        "experience": "Experience",
                        "education": "Education",
                        "extras": "Extras",
                    }
                    for key in ["skills", "experience", "education", "extras"]:
                        detail = item["breakdown"][key]
                        category_score = detail["score"]
                        category_max = detail["max"] if detail["max"] > 0 else 1
                        ratio = min(max(category_score / category_max, 0), 1)
                        st.write(f"{label_map[key]}: {category_score:.1f}/{detail['max']:.1f}")
                        st.progress(ratio)

        with st.expander("🔧 Show Raw Agent Outputs (Technical View)", expanded=False):
            tabs = st.tabs([item["candidate"] for item in candidate_results])
            for tab, item in zip(tabs, candidate_results):
                with tab:
                    st.markdown("**Agent Flow Replay**")
                    st.markdown("\n".join(item.get("flow_events", [])) or "No flow events captured")
                    st.markdown("**Resume Agent**")
                    st.code(item["agent_outputs"]["Resume_agent"] or "No output")
                    st.markdown("**JD Agent**")
                    st.code(item["agent_outputs"]["JD_agent"] or "No output")
                    st.markdown("**Redflag Agent**")
                    st.code(item["agent_outputs"]["Redflag_agent"] or "No output")
                    st.markdown("**Recruiter Agent**")
                    st.code(item["agent_outputs"]["Recruiter_agent"] or "No output")
    else:
        st.info("Run **Match Resumes** to generate candidate analysis.")

if __name__ == "__main__":
    main()
