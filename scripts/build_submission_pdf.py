"""
Build the instructor-facing submission PDF.

Run:
    python scripts/build_submission_pdf.py

Output:
    docs/SUBMISSION.pdf
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "docs" / "SUBMISSION.pdf"
REPO_URL = (
    "https://github.com/ElorabiWassim/"
    "Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data"
)

# Colour palette (matches the style of the existing report.pdf)
NAVY = colors.HexColor("#1F3A5F")
ACCENT = colors.HexColor("#2E78B5")
LIGHT_BG = colors.HexColor("#F1F4F8")
GREY = colors.HexColor("#555555")
TABLE_HEADER_BG = colors.HexColor("#1F3A5F")


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def make_styles() -> dict:
    """Create and return a dictionary of ParagraphStyle objects for the PDF.
    
    Returns:
        dict: A dictionary mapping style names to ParagraphStyle instances
              containing 'title', 'subtitle', 'h1', 'h2', 'body', 'bullet',
              'mono', 'callout', and 'small' styles.
    
    Note:
        Styles inherit from ReportLab's base sample stylesheet and are
        customized with specific fonts, sizes, colors, and spacing to
        match the project's visual identity (NAVY blue primary, ACCENT
        secondary, consistent padding/margins).
    """
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontName="Helvetica-Bold", fontSize=22, leading=26,
            textColor=NAVY, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"],
            fontName="Helvetica", fontSize=13, leading=16,
            textColor=GREY, spaceAfter=22,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"],
            fontName="Helvetica-Bold", fontSize=15, leading=19,
            textColor=NAVY, spaceBefore=14, spaceAfter=8,
            borderPadding=(0, 0, 4, 0),
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=12, leading=15,
            textColor=ACCENT, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontName="Helvetica", fontSize=10, leading=13.5,
            alignment=TA_LEFT, spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["BodyText"],
            fontName="Helvetica", fontSize=10, leading=13.5,
            leftIndent=14, bulletIndent=4, spaceAfter=3,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Code"],
            fontName="Courier", fontSize=9, leading=12,
            textColor=colors.black, backColor=LIGHT_BG,
            borderPadding=(6, 6, 6, 6),
            leftIndent=0, rightIndent=0, spaceAfter=8,
        ),
        "callout": ParagraphStyle(
            "callout", parent=base["BodyText"],
            fontName="Helvetica", fontSize=10, leading=13.5,
            textColor=colors.HexColor("#7A2E2E"),
            backColor=colors.HexColor("#FBECEC"),
            borderColor=colors.HexColor("#C45757"),
            borderWidth=0.6, borderPadding=(8, 8, 8, 8),
            spaceBefore=4, spaceAfter=10,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"],
            fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=GREY,
        ),
    }
    return styles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def header_footer(canvas, doc):
    """Draw the header and footer on each page of the PDF.
    
    Args:
        canvas: ReportLab canvas object for drawing
        doc: Document object containing page number information
    
    Effects:
        - Draws a horizontal rule line near the top of the page
        - Adds header text (left-aligned title, right-aligned group info)
        - Adds centered page number at the bottom of each page
    
    Note:
        This function is called automatically by ReportLab's BaseDocTemplate
        on each page via the onPage parameter of PageTemplate.
    """
    canvas.saveState()
    width, height = A4
    # Top rule
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(0.6)
    canvas.line(2 * cm, height - 1.6 * cm, width - 2 * cm, height - 1.6 * cm)
    # Top text
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(NAVY)
    canvas.drawString(2 * cm, height - 1.3 * cm,
                      "Global Flood Event Data Pipeline  -  Submission Brief")
    canvas.drawRightString(width - 2 * cm, height - 1.3 * cm,
                           "Group G7  -  Project 4  -  AY 2025-2026")
    # Bottom page number
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(GREY)
    canvas.drawCentredString(width / 2.0, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


def make_doc() -> BaseDocTemplate:
    """Create and configure the ReportLab document template.
    
    Returns:
        BaseDocTemplate: Configured document with A4 page size, custom margins,
                         and a single frame with the header/footer callback.
    
    Note:
        The document uses 2cm margins on all sides and a single content frame
        that spans the entire printable area between margins.
    """
    doc = BaseDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="Global Flood Event Data Pipeline - Submission Brief",
        author="Group G7",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="content",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=header_footer)])
    return doc


def styled_table(data, col_widths, header=True):
    """Create a styled ReportLab Table with consistent formatting.
    
    Args:
        data: List of lists containing table cell content
        col_widths: List of column widths in cm or points
        header: Boolean indicating whether the first row is a header row
    
    Returns:
        Table: Configured ReportLab Table object with applied TableStyle
    
    Note:
        Tables have alternating row backgrounds (white and LIGHT_BG),
        navy blue borders, and appropriate padding. Header rows (if enabled)
        receive a dark navy background with white bold text.
    """
    style_cmds = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1),
         [colors.white, LIGHT_BG]),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, NAVY),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, NAVY),
    ]
    if header:
        style_cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
        ]
    tbl = Table(data, colWidths=col_widths, hAlign="LEFT")
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
def build_story(styles):
    """Build the complete PDF story (content flow) as a list of flowables.
    
    Args:
        styles: Dictionary of ParagraphStyle objects from make_styles()
    
    Returns:
        list: A list of ReportLab flowable objects (Paragraphs, Spacers,
              Tables, PageBreaks) representing the complete document content.
    
    Note:
        This function constructs the submission document with 9 main sections:
        1. Project description
        2. Deliverables checklist
        3. Architecture overview
        4. Verification instructions
        5. Key metrics
        6. Limitations
        7. Operational notes
        8. File map
        9. Sign-off
    """
    P = lambda txt, key="body": Paragraph(txt, styles[key])
    story = []

    # ---- Cover block ----
    story.append(P("Global Flood Event Data Pipeline", "title"))
    story.append(P(
        "Submission Brief for the Instructor &mdash; Project 4 "
        "(Data Engineering &amp; Big Data Track)", "subtitle"))

    cover_data = [
        ["Module",        "Final Year Project Propositions"],
        ["Track",         "Group 1 &mdash; Data Engineering &amp; Big Data (Project 4)"],
        ["Group",         "G7"],
        ["Instructor",    "Dr. Meziane Iftene"],
        ["Academic year", "2025&ndash;2026"],
        ["Repository",
         f'<font color="#2E78B5"><link href="{REPO_URL}">{REPO_URL}</link></font>'],
    ]
    cover_rows = [[P(k, "body"), P(v, "body")] for k, v in cover_data]
    story.append(styled_table(cover_rows, col_widths=[3.6 * cm, 12.4 * cm],
                              header=False))
    story.append(Spacer(1, 10))

    # ---- 1. What this project is ----
    story.append(P("1. What this project is (in one paragraph)", "h1"))
    story.append(P(
        "The Global Flood Event Data Pipeline is an end-to-end, reproducible "
        "data-engineering platform that fuses six heterogeneous flood data "
        "streams &mdash; satellite-derived archives (Dartmouth HDX &amp; live "
        "MasterList, Copernicus EMS), authoritative disaster registries "
        "(EM-DAT), humanitarian situation reports (ReliefWeb), and public "
        "social-media signals (Bluesky) &mdash; into a single unified "
        "PostgreSQL/PostGIS warehouse with Uber H3 spatial indexing. The "
        "pipeline is orchestrated by Apache Airflow (9-task DAG with a 6-way "
        "parallel ingestion fan-out), modeled in a raw &rarr; staging &rarr; "
        "marts medallion architecture (also expressed as a parallel dbt "
        "project), validated by 15 automated SQL data-quality checks, "
        "covered by 117 unit tests, and exposed through a 15-route FastAPI "
        "service plus an interactive dashboard."))

    # ---- 2. Submission checklist ----
    story.append(P("2. Submission deliverables checklist", "h1"))
    story.append(P(
        "Every required deliverable from the Project 4 brief is mapped below "
        "to a concrete artifact in the repository.", "body"))

    checklist = [
        ["#", "Required deliverable", "Where it lives", "Status"],
        ["1", "Production codebase (shared repository)",
         "GitHub repo (link above); branch main", "Done"],
        ["2", "Infrastructure: docker-compose.yml + .env.example",
         "docker-compose.yml &amp; .env.example at repo root", "Done"],
        ["3", "Data-warehouse layer: dbt project (staging + marts) "
              "+ documented PostGIS ERD",
         "dbt/ (dbt_project.yml, models/staging, models/marts), "
         "db/schema.sql, docs/erd.md (Mermaid), docs/erd.dbml", "Done"],
        ["4", "API &amp; validation modules: unit tests, DQ checks, "
              "route definitions",
         "tests/ (117 tests across 5 modules), "
         "validation/data_quality.py (15 SQL checks), "
         "api/main.py (15 REST routes)", "Done"],
        ["5", "Analytical outputs: time-series, seasonal decomposition, "
              "per-basin frequency lines",
         "notebooks/time_series_analysis.ipynb (reads from "
         "marts.flood_events_by_month, marts.flood_frequency_by_basin)", "Done"],
        ["6", "Detailed README on the main branch",
         "README.md (15 sections; absolute setup, config, API, DQ, "
         "troubleshooting)", "Done"],
        ["7", "Engineering report (canvas template, fully populated)",
         "docs/group_project_report.tex (compiles to PDF with pdflatex)", "Done"],
    ]
    rows = [[P(c, "small") for c in row] for row in checklist]
    story.append(styled_table(rows,
                              col_widths=[0.8 * cm, 4.4 * cm, 7.6 * cm, 3.2 * cm]))

    story.append(Spacer(1, 6))
    story.append(P(
        "<b>Note on numbers.</b> The brief mentions &quot;64/64 unit tests, "
        "7/7 DQ checks, 8 REST endpoints&quot;. The repository has grown "
        "beyond those figures during implementation: 117 unit tests, 15 DQ "
        "checks (7 flood-event + 8 social-signal), and 15 REST routes. The "
        "<b>7 flood-event DQ checks match the brief exactly</b>; the social "
        "layer added 8 more.", "body"))

    # ---- 3. Architecture at a glance ----
    story.append(P("3. Architecture at a glance", "h1"))
    story.append(P("Medallion warehouse:", "h2"))
    story.append(P(
        "<b>raw</b> &mdash; 6 JSONB tables, one per source, plus an "
        "ingestion audit log. Every payload is preserved untouched with a "
        "batch_id and ingested_at timestamp.", "bullet"))
    story.append(P(
        "<b>staging</b> &mdash; 2 canonical tables (flood_events, "
        "social_flood_signals) with WGS-84 PostGIS Point geometries, GiST "
        "indexes, and H3 resolution-7 hexagonal indexing.", "bullet"))
    story.append(P(
        "<b>marts</b> &mdash; 8 API-ready views (flood_events_unique, "
        "flood_events_by_region, flood_events_by_month, "
        "flood_events_by_source, flood_frequency_by_basin, "
        "social_signals_by_country_day, flood_events_with_social_signals, "
        "social_flood_signals). The FastAPI service reads ONLY from marts.",
        "bullet"))

    story.append(P("Airflow DAG (flood_event_pipeline):", "h2"))
    story.append(Paragraph(
        "schema_setup &rarr; [dartmouth || glofas || copernicus_ems || "
        "emdat || reliefweb || bluesky] &rarr; transform &rarr; build_marts "
        "&rarr; dq_check &nbsp;(trigger_rule=&quot;all_done&quot;)",
        styles["mono"]))

    story.append(P("Container topology:", "h2"))
    story.append(P(
        "Two services declared in docker-compose.yml: <b>airflow</b> "
        "(custom image with pandas + h3 + dbt + SQLAlchemy baked in, "
        "exposes port 8081) and <b>api</b> (FastAPI on port 8000). State "
        "is held in the external PostgreSQL/PostGIS warehouse (Supabase by "
        "default; any Postgres+PostGIS will work).", "body"))

    # ---- 4. How to verify each deliverable ----
    story.append(PageBreak())
    story.append(P("4. How the instructor can verify each deliverable", "h1"))

    story.append(P("4.1 &nbsp; Bring the stack up (Docker path)", "h2"))
    story.append(Paragraph(
        "git clone {repo}<br/>"
        "cd Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data<br/>"
        "cp .env.example .env&nbsp;&nbsp;&nbsp;# then set DATABASE_URL<br/>"
        "docker compose up --build -d<br/>"
        "docker compose logs -f airflow&nbsp;&nbsp;&nbsp;# wait for "
        "'Webserver started'".format(repo=REPO_URL),
        styles["mono"]))
    story.append(P(
        "Airflow UI &rarr; <font color='#2E78B5'>"
        "<link href='http://localhost:8081'>http://localhost:8081</link>"
        "</font> (admin / admin).<br/>"
        "FastAPI Swagger &rarr; <font color='#2E78B5'>"
        "<link href='http://localhost:8000/docs'>http://localhost:8000/docs"
        "</link></font>.<br/>"
        "Dashboard &rarr; <font color='#2E78B5'>"
        "<link href='http://localhost:8000/'>http://localhost:8000/</link>"
        "</font>.", "body"))

    story.append(P("4.2 &nbsp; Trigger one full pipeline run", "h2"))
    story.append(Paragraph(
        "docker exec -it flood_airflow airflow dags trigger "
        "flood_event_pipeline",
        styles["mono"]))
    story.append(P(
        "Then open the Airflow DAG view to see the 9 tasks complete "
        "(schema_setup &rarr; 6 parallel ingestions &rarr; transform &rarr; "
        "build_marts &rarr; dq_check).", "body"))

    story.append(P("4.3 &nbsp; Inspect the data-quality report", "h2"))
    story.append(Paragraph(
        "docker exec -it flood_airflow cat "
        "/opt/airflow/data/logs/data_quality_report.md",
        styles["mono"]))
    story.append(P(
        "On a clean run, all 15 checks return PASS with 0 violation rows.",
        "body"))

    story.append(P("4.4 &nbsp; Run the 117 unit tests", "h2"))
    story.append(Paragraph(
        "python -m venv .venv &amp;&amp; .venv\\Scripts\\activate<br/>"
        "pip install -r requirements/base.txt -r requirements/dev.txt<br/>"
        "pytest -v",
        styles["mono"]))

    story.append(P("4.5 &nbsp; Smoke-test every REST route", "h2"))
    story.append(Paragraph(
        "curl http://localhost:8000/health<br/>"
        "curl &quot;http://localhost:8000/flood-events?limit=5&quot;<br/>"
        "curl &quot;http://localhost:8000/flood-events/by-region?country=Vietnam&quot;<br/>"
        "curl &quot;http://localhost:8000/flood-events/by-time?start=2020-01-01&amp;end=2020-12-31&quot;<br/>"
        "curl &quot;http://localhost:8000/analytics/frequency-by-basin?basin=Vietnam&quot;<br/>"
        "curl &quot;http://localhost:8000/analytics/social-signals/by-platform&quot;",
        styles["mono"]))
    story.append(P(
        "Full route inventory (15 endpoints) is in README.md section 10 and "
        "auto-documented at /docs.", "body"))

    story.append(P("4.6 &nbsp; Browse the dbt project", "h2"))
    story.append(Paragraph(
        "docker exec -it flood_airflow bash -lc &quot;cd /opt/airflow/dbt &amp;&amp; "
        "dbt run --profiles-dir .&quot;<br/>"
        "docker exec -it flood_airflow bash -lc &quot;cd /opt/airflow/dbt &amp;&amp; "
        "dbt docs generate --profiles-dir . &amp;&amp; dbt docs serve --profiles-dir .&quot;",
        styles["mono"]))

    story.append(P("4.7 &nbsp; Read the engineering report and ERD", "h2"))
    story.append(P(
        "<b>Report</b> &mdash; docs/group_project_report.tex "
        "(compile with <font face='Courier'>pdflatex docs/group_project_report.tex</font>; "
        "run twice for the table of contents).<br/>"
        "<b>ERD</b> &mdash; docs/erd.md renders as a Mermaid diagram inline on "
        "GitHub. docs/erd.dbml can be pasted into dbdiagram.io for a "
        "polished PNG/PDF export.", "body"))

    # ---- 5. Key numbers ----
    story.append(PageBreak())
    story.append(P("5. Key numbers (code-derived, not estimated)", "h1"))
    numbers = [
        ["Metric", "Value", "Defined in"],
        ["Raw data sources",                  "6",   "ingestion/ingest_*.py"],
        ["Raw tables (raw.*)",                 "6",   "db/schema.sql"],
        ["Staging tables (staging.*)",         "2",   "db/schema.sql"],
        ["Mart views (marts.*)",               "8",   "transformations/marts.py"],
        ["Airflow DAG tasks",                  "9",   "dags/flood_event_pipeline_dag.py"],
        ["Parallel ingestion tasks",           "6",   "dags/flood_event_pipeline_dag.py"],
        ["REST endpoints",                     "15",  "api/main.py"],
        ["Source normalizers",                 "5",   "transformations/transform.py"],
        ["DQ checks (flood-event group)",      "7",   "validation/data_quality.py"],
        ["DQ checks (social-signal group)",    "8",   "validation/data_quality.py"],
        ["DQ checks (total)",                  "15",  "validation/data_quality.py"],
        ["Unit tests (pytest, 5 modules)",     "117", "tests/"],
        ["Bluesky keywords",                   "29",  "ingest_bluesky.DEFAULT_KEYWORDS"],
        ["Bluesky strong terms",               "22",  "ingest_bluesky.STRONG_FLOOD_TERMS"],
        ["Bluesky context terms",              "37",  "ingest_bluesky.DEFAULT_CONTEXT_TERMS"],
        ["Bluesky excluded phrases",           "40",  "ingest_bluesky.DEFAULT_EXCLUDED_PHRASES"],
        ["Bluesky regex patterns",             "3",   "ingest_bluesky.py"],
        ["H3 resolution",                      "7",   "config/settings.py"],
        ["DFO MasterList / HDX overlap",       "~73%", "marts.py comments"],
        ["DAG schedule",                       "@daily", "flood_event_pipeline_dag.py"],
    ]
    rows = [[P(c, "small") for c in row] for row in numbers]
    story.append(styled_table(rows, col_widths=[7.0 * cm, 2.0 * cm, 7.0 * cm]))

    # ---- 6. What is NOT in scope ----
    story.append(Spacer(1, 6))
    story.append(P("6. Documented limitations &amp; out-of-scope items", "h1"))
    story.append(P(
        "<b>GloFAS reanalysis grids</b> are not ingested today. The "
        "ingest_glofas.py module currently pulls the Dartmouth MasterList "
        "(documented). A real Copernicus CDS branch is a documented next "
        "step.", "bullet"))
    story.append(P(
        "<b>Polygon geometries</b> are not persisted today; centroids are "
        "stored as PostGIS Points and the polygon is dropped after H3 "
        "indexing.", "bullet"))
    story.append(P(
        "<b>Geocoding</b> for social posts is rule-based (country, "
        "adjective, US state, city-state, timezone patterns). NER / "
        "gazetteer-backed extraction is a documented next step.", "bullet"))
    story.append(P(
        "<b>Executor</b>: SequentialExecutor is used for MVP simplicity. "
        "LocalExecutor + Postgres metadata DB is the documented upgrade.",
        "bullet"))
    story.append(P(
        "<b>Auth on the API</b>: open CORS for development; restrict to "
        "known origins behind a reverse proxy before public release.",
        "bullet"))

    # ---- 7. Operational notes ----
    story.append(P("7. Operational notes for the reviewer", "h1"))
    story.append(P(
        "<b>Database</b>: the pipeline targets any PostgreSQL 14+ with the "
        "PostGIS 3.x extension. We tested against Supabase&apos;s pgBouncer "
        "pooler; the SQLAlchemy engine in db/client.py is tuned for that "
        "case (pool_pre_ping=True, pool_recycle=300s, TCP keep-alive "
        "30/10/5) and wraps writes in an exponential-backoff retry.", "body"))
    story.append(P(
        "<b>Reproducibility</b>: each ingestion module ships with a "
        "committed seed CSV/JSON fallback under data/raw/&lt;source&gt;/. "
        "If the remote source is unreachable, the run still completes "
        "against the fallback and writes a .meta.json sidecar with "
        "used_fallback=true and the reason.", "body"))
    story.append(P(
        "<b>Idempotency</b>: every upsert is keyed on a natural composite "
        "(e.g. (source, source_event_id) for flood events, (platform, "
        "post_id) for social posts) with ON CONFLICT DO UPDATE. Re-running "
        "the DAG produces the same warehouse state.", "body"))
    story.append(P(
        "<b>Secrets</b>: .env is local-only and not committed. .env.example "
        "is the template (root of repo). The pipeline reads every setting "
        "from environment variables via config/settings.py.", "body"))

    # ---- 8. File map ----
    story.append(P("8. Where to find each artifact", "h1"))
    files = [
        ["Artifact", "Path"],
        ["Production code (root)",            "."],
        ["FastAPI service",                   "api/main.py"],
        ["Airflow DAG",                       "dags/flood_event_pipeline_dag.py"],
        ["Ingestion modules",                 "ingestion/ingest_*.py"],
        ["Transformations",                   "transformations/transform.py, marts.py, social_geo.py"],
        ["Data-quality checks",               "validation/data_quality.py"],
        ["Database client &amp; schema",      "db/client.py, db/schema.sql"],
        ["dbt project",                       "dbt/ (dbt_project.yml, profiles.yml, models/)"],
        ["Unit tests",                        "tests/ (conftest.py + 5 modules)"],
        ["Notebook",                          "notebooks/time_series_analysis.ipynb"],
        ["Docker manifest",                   "docker-compose.yml"],
        ["Airflow Dockerfile",                "airflow/Dockerfile"],
        ["FastAPI Dockerfile",                "api/Dockerfile"],
        ["Requirements (runtime / dev / nb)", "requirements/base.txt, dev.txt, notebooks.txt"],
        ["README (15 sections)",              "README.md"],
        ["Engineering report (LaTeX)",        "docs/group_project_report.tex"],
        ["ERD (Mermaid, GitHub-rendered)",    "docs/erd.md"],
        ["ERD (DBML for dbdiagram.io)",       "docs/erd.dbml"],
        ["Data-source catalogue",             "docs/data_sources.md"],
    ]
    rows = [[P(c, "small") for c in row] for row in files]
    story.append(styled_table(rows, col_widths=[6.0 * cm, 10.0 * cm]))

    # ---- 9. Sign-off ----
    story.append(Spacer(1, 12))
    story.append(P(
        "<i>Document generated from the repository state on the main "
        "branch. Run <font face='Courier'>python scripts/build_submission_pdf.py</font> "
        "to regenerate this PDF after future changes.</i>", "small"))

    return story


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Build the submission PDF document.
    
    Effects:
        - Creates the docs/ directory if it doesn't exist
        - Generates a PDF at OUTPUT_PATH using ReportLab
        - Prints a confirmation message with the output file location
    
    Note:
        This is the main entry point when the script is run directly.
        The PDF includes all sections defined in build_story() and
        uses the header/footer defined in header_footer().
    """
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    styles = make_styles()
    doc = make_doc()
    doc.build(build_story(styles))
    print(f"Wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()


# =============================================================================
# UNUSED SUPPORT FUNCTIONS
# These functions are defined but NEVER called anywhere in the code.
# They exist as documentation/examples only and do not affect execution.
# =============================================================================

def _validate_color_hex(color_hex: str) -> bool:
    """Validate that a string is a properly formatted hex color code.
    
    Args:
        color_hex: String like '#1F3A5F' or '1F3A5F'
    
    Returns:
        True if the string is a valid hex color code (with or without # prefix),
        False otherwise.
    
    Example:
        >>> _validate_color_hex("#1F3A5F")
        True
        >>> _validate_color_hex("ZZZZZZ")
        False
    
    Note:
        This function is never called. The color palette is hardcoded
        as NAVY, ACCENT, LIGHT_BG, GREY, TABLE_HEADER_BG constants.
    """
    import re
    # Remove # prefix if present
    hex_str = color_hex.lstrip('#')
    # Valid hex color is 6 characters, 0-9 A-F a-f
    return bool(re.match(r'^[0-9A-Fa-f]{6}$', hex_str))


def _calculate_text_width(text: str, font_size: float = 10, font_name: str = "Helvetica") -> float:
    """Estimate the width of text in points for layout calculations.
    
    Args:
        text: The text string to measure
        font_size: Font size in points
        font_name: Font name (Helvetica, Courier, etc.)
    
    Returns:
        Estimated width in points
    
    Note:
        This function is never called. ReportLab handles text measurements
        internally through its canvas.stringWidth() method when rendering.
        This is a simplified estimation that doesn't account for kerning,
        ligatures, or proportional font variations.
    """
    # Rough approximation: average character width ~ 0.6 * font_size points
    # Helvetica at 10pt averages about 5-6 points per character
    avg_char_width = font_size * 0.6
    return len(text) * avg_char_width


def _get_page_dimensions() -> tuple[float, float]:
    """Return the A4 page dimensions in points and centimeters.
    
    Returns:
        tuple: (width_pt, height_pt, width_cm, height_cm) where:
            - width_pt: A4 width in points (595.28)
            - height_pt: A4 height in points (841.89)
            - width_cm: A4 width in cm (21.0)
            - height_cm: A4 height in cm (29.7)
    
    Note:
        This function is never called. A4 dimensions are imported directly
        from reportlab.lib.pagesizes and used inline wherever needed.
    """
    from reportlab.lib.pagesizes import A4
    width_pt, height_pt = A4
    width_cm = 21.0
    height_cm = 29.7
    return (width_pt, height_pt, width_cm, height_cm)


def _truncate_text_to_width(text: str, max_width: float, font_size: float = 10) -> str:
    """Truncate text to fit within a specified width, adding ellipsis.
    
    Args:
        text: The text to potentially truncate
        max_width: Maximum allowed width in points
        font_size: Font size in points
    
    Returns:
        Truncated string with '...' appended if needed, or original string
    
    Note:
        This function is never called. Table cells in the PDF automatically
        wrap text or can be configured with wordWrap='CJK' or similar.
        Manual truncation is not used in the current PDF generation.
    """
    estimated_width = len(text) * font_size * 0.6
    if estimated_width <= max_width:
        return text
    
    # Binary search for the truncation point
    ellipsis = "..."
    max_len = len(text)
    low, high = 0, max_len
    while low < high:
        mid = (low + high + 1) // 2
        test_width = len(text[:mid] + ellipsis) * font_size * 0.6
        if test_width <= max_width:
            low = mid
        else:
            high = mid - 1
    
    return text[:low] + ellipsis


def _create_metadata_dict(
    title: str,
    author: str,
    subject: str = "",
    keywords: list[str] | None = None
) -> dict:
    """Create a PDF metadata dictionary for document info.
    
    Args:
        title: Document title
        author: Document author
        subject: Document subject/description
        keywords: List of keyword strings
    
    Returns:
        Dictionary with title, author, subject, and keywords fields
    
    Example:
        >>> _create_metadata_dict("My Doc", "Jane Doe", keywords=["flood", "pipeline"])
        {'title': 'My Doc', 'author': 'Jane Doe', 'subject': '', 'keywords': 'flood, pipeline'}
    
    Note:
        This function is never called. PDF metadata is set directly in
        BaseDocTemplate constructor via the title and author parameters.
    """
    metadata = {
        "title": title,
        "author": author,
        "subject": subject,
    }
    if keywords:
        metadata["keywords"] = ", ".join(keywords)
    return metadata


def _cm_to_points(cm_value: float) -> float:
    """Convert centimeters to points (ReportLab's unit).
    
    Args:
        cm_value: Measurement in centimeters
    
    Returns:
        Equivalent measurement in points (1 cm = 28.3464567 points)
    
    Example:
        >>> _cm_to_points(2.0)  # 2 cm to points
        56.6929134
    
    Note:
        This function is never called. The conversion constant (cm) is
        imported from reportlab.lib.units and used directly in the code
        (e.g., 2 * cm, 1.6 * cm, etc.). ReportLab's 'cm' object handles
        the multiplication internally.
    """
    # 1 cm = 28.346456692913385 points (72 points per inch / 2.54 cm per inch)
    return cm_value * 28.346456692913385