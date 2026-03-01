import os
import json
import uuid
from flask import Flask, render_template, request, session, jsonify
from dotenv import load_dotenv
from pypdf import PdfReader
import docx


# 1. NEW IMPORTS
from langfuse import Langfuse 
from langfuse.decorators import observe, langfuse_context
from langfuse.openai import AzureOpenAI

from drive_utils import upload_resume_to_drive
from supabase_log import get_client_ip, log_upload_to_supabase

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

# Server-side cache for resume data (cookie can't hold 100k chars). Keyed by UUID in session.
_improvements_cache = {}

# Max resume length: ~100k chars fits safely in 128k context (prompt + response).
# Reject larger so we don't truncate or overflow.
MAX_RESUME_CHARS = 100_000

# 2. INITIALIZE LANGFUSE CLIENT (For Prompts)
langfuse = Langfuse()

# 3. SETUP AZURE CLIENT
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT_NAME")

# --- UTILS ---
def extract_text_from_pdf(file):
    reader = PdfReader(file)
    text = "".join([page.extract_text() or "" for page in reader.pages])
    return text

def extract_text_from_docx(file):
    doc = docx.Document(file)
    return "\n".join([para.text for para in doc.paragraphs])

# --- LLM FUNCTIONS ---

@observe()
def get_structured_analysis(text):
    # Use full text (already validated length in analyze())
    langfuse_prompt = langfuse.get_prompt("resume_analysis")
    compiled_prompt = langfuse_prompt.compile(text=text)

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[
            {"role": "system", "content": "You are a specialized career strategist who hates generic advice."},
            {"role": "user", "content": compiled_prompt}
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
        langfuse_prompt=langfuse_prompt
    )

    return json.loads(response.choices[0].message.content)


@observe()
def generate_roast(text, score):
    langfuse_prompt = langfuse.get_prompt("resume_roast")
    compiled_prompt = langfuse_prompt.compile(text=text, score=score)

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[{"role": "user", "content": compiled_prompt}],
        temperature=0.8,
        langfuse_prompt=langfuse_prompt
    )

    return response.choices[0].message.content


@observe()
def get_improvements(text, score, reasoning):
    """Separate prompt for action plan; fine-tune independently from analysis."""
    langfuse_prompt = langfuse.get_prompt("resume_improvements")
    compiled_prompt = langfuse_prompt.compile(text=text, score=score, reasoning=reasoning)

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[{"role": "user", "content": compiled_prompt}],
        temperature=0.4,
        response_format={"type": "json_object"},
        langfuse_prompt=langfuse_prompt
    )

    data = json.loads(response.choices[0].message.content)
    return data.get("improvements", [])


# --- REST OF YOUR APP ---

def calculate_score(data):
    score = (0.4 * data["repetitive_score"] - 0.2 * data["leadership_score"] - 
             0.2 * data["strategy_score"] - 0.2 * data["ai_exposure_score"])
    return max(0, min(10, round(score + 6, 1)))

def get_risk_status(score):
    if score < 3.0: return {"label": "AI-RESISTANT", "color": "text-emerald-500", "subtext": "Safe for now.", "border": "border-emerald-900/30", "bg": "bg-emerald-950/10"}
    elif score < 5.0: return {"label": "2-YEAR CLOCK", "color": "text-orange-400", "subtext": "Pivot required soon.", "border": "border-orange-900/30", "bg": "bg-orange-950/10"}
    elif score < 7.5: return {"label": "URGENT PIVOT", "color": "text-red-500", "subtext": "Automation is eating your tasks.", "border": "border-red-900/30", "bg": "bg-red-950/10"}
    else: return {"label": "CRITICAL RISK", "color": "text-red-600", "subtext": "Immediate obsolescence.", "border": "border-red-900/50", "bg": "bg-red-900/20"}

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
@observe()
def analyze():
    file = request.files["file"]
    filename = file.filename

    langfuse_context.update_current_trace(
        name="Resume Scan",
        metadata={"filename": filename}
    )

    if filename.lower().endswith(".pdf"):
        text = extract_text_from_pdf(file)
        mimetype = "application/pdf"
    elif filename.lower().endswith(".docx"):
        text = extract_text_from_docx(file)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        return "Invalid file type", 400

    upload_resume_to_drive(file, filename, mimetype)

    if len(text) > MAX_RESUME_CHARS:
        return render_template(
            "resume_too_long.html",
            max_chars=MAX_RESUME_CHARS,
            actual_chars=len(text)
        ), 200

    structured = get_structured_analysis(text)
    score = calculate_score(structured)
    roast = generate_roast(text, score)
    status = get_risk_status(score)
    reasoning = structured.get("reasoning", "")

    rep = structured.get("repetitive_score", 0)
    lead = structured.get("leadership_score", 0)
    strat = structured.get("strategy_score", 0)
    ai_exp = structured.get("ai_exposure_score", 0)

    # Log to Supabase (IP, filename, extracted PII, score, individual scores) — no-op if env not set
    log_upload_to_supabase(
        get_client_ip(request),
        filename,
        extracted_name=structured.get("extracted_name"),
        extracted_email=structured.get("extracted_email"),
        extracted_phone=structured.get("extracted_phone"),
        score=score,
        repetitive_score=rep,
        leadership_score=lead,
        strategy_score=strat,
        ai_exposure_score=ai_exp,
        user_agent=request.headers.get("User-Agent"),
    )

    # Build optional score breakdown for UI (individual scores, per-score reasoning, formula)
    raw_value = 0.4 * rep - 0.2 * lead - 0.2 * strat - 0.2 * ai_exp
    score_breakdown = {
        "repetitive_score": rep,
        "leadership_score": lead,
        "strategy_score": strat,
        "ai_exposure_score": ai_exp,
        "repetitive_reasoning": structured.get("repetitive_reasoning", ""),
        "leadership_reasoning": structured.get("leadership_reasoning", ""),
        "strategy_reasoning": structured.get("strategy_reasoning", ""),
        "ai_exposure_reasoning": structured.get("ai_exposure_reasoning", ""),
        "reasoning": reasoning,
        "raw_value": round(raw_value, 2),
        "final_score": score,
    }

    # Store for on-demand improvements (separate prompt); server-side cache (cookie can't hold full text)
    cache_key = str(uuid.uuid4())
    _improvements_cache[cache_key] = {"text": text, "score": score, "reasoning": reasoning}
    session["improvements_key"] = cache_key

    return render_template(
        "result.html",
        filename=filename,
        score="{:.1f}".format(score),
        status=status,
        roast=roast,
        reasoning=reasoning,
        score_breakdown=score_breakdown,
    )


@app.route("/get-improvements", methods=["POST"])
@observe()
def api_get_improvements():
    """Generate action plan via separate Langfuse prompt (resume_improvements)."""
    cache_key = session.get("improvements_key")
    if not cache_key:
        return jsonify({"error": "No recent analysis. Upload a resume first."}), 400
    data = _improvements_cache.get(cache_key)
    if data is None:
        return jsonify({"error": "Session expired. Upload a resume again to get improvements."}), 400
    try:
        improvements = get_improvements(data["text"], data["score"], data["reasoning"])
        _improvements_cache.pop(cache_key, None)
        return jsonify({"improvements": improvements})
    except Exception as e:
        return jsonify({"error": "Could not generate improvements. Check that prompt 'resume_improvements' exists in Langfuse."}), 500

if __name__ == "__main__":
    app.run(debug=True)