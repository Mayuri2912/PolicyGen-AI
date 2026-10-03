"""End-to-end demonstration against a RUNNING server, over real HTTP.

Start the backend first (see README), then from the project root:

    backend\\.venv\\Scripts\\python.exe scripts\\e2e_demo.py
    backend\\.venv\\Scripts\\python.exe scripts\\e2e_demo.py --base-url http://127.0.0.1:8000 --keep

It walks the whole workflow the way the web app does, including background
jobs and polling:

    upload -> extract -> analyze -> review findings -> recommend -> generate
    -> preview -> edit (new version) -> compare -> download DOCX and PDF

The downloaded documents are written to ``generated_reports/``.  Without
``--keep`` the files, analysis and report it created are deleted afterwards.
The script uses whichever LLM provider the server is configured with; in mock
mode the results are simulated and are labelled as such.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import docx
import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_files"
OUTPUT = ROOT / "generated_reports"
FILES = (
    "sample_security.txt",
    "sample_risk_assessment.xlsx",
    "sample_vendor_assessment.docx",
    "sample_cloud_security.pdf",
    "sample_access_records.csv",
)
TERMINAL = {"completed", "failed", "cancelled"}


class Demo:
    def __init__(self, base_url: str, token: str):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=base_url.rstrip("/") + "/api", headers=headers, timeout=120)
        self.step_number = 0

    def step(self, title: str) -> None:
        self.step_number += 1
        print(f"\n[{self.step_number}] {title}")

    def call(self, method: str, path: str, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(f"FAILED {method} {path}: {response.status_code} {response.text[:400]}")
        return response

    def wait_for_job(self, job_id: str, label: str) -> dict:
        seen = None
        deadline = time.time() + 900
        while time.time() < deadline:
            job = self.call("GET", f"/jobs/{job_id}").json()
            if (job["stage"], job["message"]) != seen:
                seen = (job["stage"], job["message"])
                print(f"    {label}: {job['stage_label']:<20} {job['progress']:>3}%  {job['message']}")
            if job["status"] in TERMINAL:
                if job["status"] != "completed":
                    raise SystemExit(f"FAILED {label}: {job['error_message']}")
                return job
            time.sleep(0.4)
        raise SystemExit(f"FAILED {label}: timed out")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="", help="API access token, if the server requires one")
    parser.add_argument("--keep", action="store_true", help="keep the uploaded files, analysis and report on the server")
    args = parser.parse_args()
    demo = Demo(args.base_url, args.token)

    demo.step("Server status")
    try:
        health = demo.call("GET", "/health").json()
    except httpx.ConnectError:
        raise SystemExit(f"The backend is not reachable at {args.base_url}. Start it first (see README).") from None
    llm = health["llm"]
    print(f"    status={health['status']}  provider={llm['provider']}  model={llm['model']}  simulated={llm['simulated']}")
    print(f"    OCR available={health['ocr']['available']}")
    if llm["simulated"]:
        print("    NOTE: mock mode - analysis and policy text are produced by rules, not by an AI model.")

    demo.step("Upload five sample documents")
    files = [("files", (name, (SAMPLES / name).read_bytes(), "application/octet-stream")) for name in FILES]
    results = demo.call("POST", "/upload", files=files).json()["results"]
    file_ids, created_ids = [], []
    for result in results:
        if not result["accepted"]:
            raise SystemExit(f"FAILED upload of {result['file_name']}: {result['error']['message']}")
        file_ids.append(result["file"]["id"])
        if not result["duplicate"]:
            created_ids.append(result["file"]["id"])
        print(f"    {result['file_name']:<34} accepted{' (already uploaded)' if result['duplicate'] else ''}")

    demo.step("Extraction (background jobs)")
    deadline = time.time() + 120
    while True:
        listed = demo.call("GET", "/files", params={"ids": ",".join(file_ids)}).json()["files"]
        if all(f["status"] in ("extracted", "failed") for f in listed) or time.time() > deadline:
            break
        time.sleep(0.3)
    for f in listed:
        doc = f["document"] or {}
        print(f"    {f['file_name']:<34} {f['status_label']:<10} {doc.get('characters', 0):>6} chars, {doc.get('sections', 0)} sections, {doc.get('tables', 0)} tables")
        if f["status"] != "extracted":
            raise SystemExit(f"FAILED extraction of {f['file_name']}: {f['error_message']}")

    demo.step("AI analysis of all five files together")
    started = demo.call("POST", "/analyses", json={"file_ids": file_ids}).json()
    demo.wait_for_job(started["job"]["id"], "analysis")
    analysis = demo.call("GET", f"/analyses/{started['analysis_id']}").json()
    print(f"    summary: {analysis['summary']['overall_summary'][:230]}...")
    print(
        f"    {len(analysis['requirements'])} requirements, {len(analysis['risks'])} risks, {len(analysis['controls'])} controls, "
        f"{len(analysis['gaps'])} gap entries, {len(analysis['contradictions'])} contradictions"
    )
    print(f"    risk levels: {analysis['risk_summary']['by_level']}")
    print(f"    gap statuses: {analysis['gap_summary']['by_status']}")
    for item in analysis["contradictions"]:
        print(f"    contradiction: {item['description']}")

    demo.step("Review findings: exclude one inferred risk, edit one requirement")
    inferred = next((r for r in analysis["risks"] if r["origin"] == "inferred"), None)
    if inferred:
        demo.call("PATCH", f"/analyses/{analysis['id']}/findings/{inferred['id']}", json={"excluded": True})
        print(f"    excluded {inferred['ref_id']} ({inferred['title']})")
    requirement = analysis["requirements"][0]
    edited = demo.call(
        "PATCH", f"/analyses/{analysis['id']}/findings/{requirement['id']}",
        json={"data": {"statement": requirement["statement"].rstrip(".") + " (confirmed by the reviewer)."}},
    ).json()
    print(f"    edited {edited['ref_id']}: user_edited={edited['user_edited']}")

    demo.step("Policy recommendation")
    recommendation = demo.call("POST", "/policies/recommend", json={"analysis_id": analysis["id"]}).json()
    for item in recommendation["recommendations"][:3]:
        print(f"    {item['name']:<32} fit={item['fit']:<8} score={item['score']}")
    print(f"    reason: {recommendation['recommended']['reasons'][0][:200]}")

    demo.step(f"Generate: {recommendation['recommended']['name']}")
    generation = demo.call(
        "POST", "/policies/generate",
        json={
            "analysis_id": analysis["id"],
            "config": {
                "policy_type": recommendation["recommended"]["policy_type"],
                "organization_name": recommendation["detected_organization_name"],
                "policy_owner": "Chief Information Security Officer",
                "review_frequency": "Annually",
            },
        },
    ).json()
    demo.wait_for_job(generation["job"]["id"], "generation")
    report_id = generation["report_id"]

    demo.step("Preview")
    report = demo.call("GET", f"/reports/{report_id}").json()
    quality = report["quality_report"]
    print(f"    title: {report['title']}  (version {report['current_version']}, simulated={report['simulated']})")
    print(f"    quality: score {quality['score']}/100, {quality['counts']['error']} errors, {quality['counts']['warning']} warnings, {len(quality['placeholders'])} placeholders")
    for section in report["document"]["sections"]:
        print(f"    {section['number']:>10}  {section['title']}")

    demo.step("Edit a section (creates version 2) and compare")
    purpose = next(s for s in report["sections"] if s["id"] == "purpose")
    demo.call(
        "PUT", f"/reports/{report_id}/sections/purpose",
        json={"markup": purpose["markup"] + "\n\nThis paragraph was added during the end-to-end demonstration.", "note": "Demo edit", "base_version": 1},
    )
    comparison = demo.call("GET", f"/reports/{report_id}/compare", params={"from": 1, "to": 2}).json()
    print(f"    v1 -> v2: {comparison['counts']}")

    demo.step("Download and verify the documents")
    OUTPUT.mkdir(exist_ok=True)
    for fmt in ("docx", "pdf"):
        response = demo.call("GET", f"/reports/{report_id}/download", params={"format": fmt})
        target = OUTPUT / f"demo_{report['policy_type']}_v2.{fmt}"
        target.write_bytes(response.content)
        if fmt == "pdf":
            reader = PdfReader(io.BytesIO(response.content))
            detail = f"{len(reader.pages)} pages, title '{reader.metadata.title}'"
            assert "end-to-end demonstration" in "".join(page.extract_text() for page in reader.pages[:8])
        else:
            document = docx.Document(io.BytesIO(response.content))
            detail = f"{len(document.paragraphs)} paragraphs, {len(document.tables)} tables"
            assert any("end-to-end demonstration" in p.text for p in document.paragraphs)
        print(f"    {fmt.upper():<5} {len(response.content):>8,d} bytes  {detail}  -> {target.relative_to(ROOT)}")

    demo.step("Report history")
    for item in demo.call("GET", "/reports").json()["reports"][:5]:
        print(f"    {item['title']:<32} v{item['current_version']}  {item['status']:<10} review={item['review_status']}")

    if not args.keep:
        demo.step("Clean up (delete the report, the analysis and the files uploaded by this run)")
        demo.call("DELETE", f"/reports/{report_id}")
        demo.call("DELETE", f"/analyses/{analysis['id']}")
        for file_id in created_ids:
            demo.call("DELETE", f"/files/{file_id}")
        assert demo.http.get(f"/reports/{report_id}").status_code == 404
        print(f"    deleted 1 report, 1 analysis, {len(created_ids)} file(s)")

    print("\nEND-TO-END DEMONSTRATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
