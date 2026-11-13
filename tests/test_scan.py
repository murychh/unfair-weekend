from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

import detector
from main import app

SAMPLES = Path(__file__).parent.parent / "samples"
client = TestClient(app)


def scan_file(name):
    with open(SAMPLES / name, "rb") as f:
        response = client.post("/api/scan", files={"file": (name, f, "application/pdf")})
    assert response.status_code == 200
    return response.json()


def make_pdf(draw):
    """Build a one-page PDF; draw(page) inserts the text under test."""
    doc = fitz.open()
    draw(doc.new_page())
    data = doc.tobytes()
    doc.close()
    return data


# Acceptance tests on the two sample resumes

def test_clean_resume_has_no_findings():
    result = scan_file("Resume_test.pdf")
    assert result["status"] == "clean"
    assert result["findings"] == []


def test_resume_with_hidden_text_is_flagged():
    result = scan_file("Resume_test1.pdf")
    assert result["status"] == "hidden_text_found"
    found = " ".join(f["text"] for f in result["findings"])
    for phrase in ["Google 2019 – 2024", "team of 12 engineers", "CISSP", "OSCP"]:
        assert phrase in found


def test_visible_text_is_not_reported_in_hidden_resume():
    result = scan_file("Resume_test1.pdf")
    found = " ".join(f["text"] for f in result["findings"])
    assert "CompTIA Security+" not in found
    assert "Alex Morgan" not in found


# One test per detection rule, on small generated PDFs

def test_rule_white_text_on_white():
    data = make_pdf(lambda p: p.insert_text((72, 100), "secret keyword", fontsize=12, color=(1, 1, 1)))
    findings = detector.scan_pdf(data)
    assert len(findings) == 1
    assert findings[0]["reason"] == detector.REASON_COLOR


def test_rule_colour_matches_non_white_background():
    def draw(page):
        page.draw_rect(fitz.Rect(50, 50, 400, 200), color=None, fill=(0.1, 0.2, 0.6))
        page.insert_text((72, 100), "visible", fontsize=12, color=(1, 1, 1))
        page.insert_text((72, 140), "hidden on blue", fontsize=12, color=(0.1, 0.2, 0.6))
    findings = detector.scan_pdf(make_pdf(draw))
    assert [f["text"] for f in findings] == ["hidden on blue"]


def test_rule_tiny_font():
    data = make_pdf(lambda p: p.insert_text((72, 100), "tiny text", fontsize=2, color=(0, 0, 0)))
    findings = detector.scan_pdf(data)
    assert len(findings) == 1
    assert detector.REASON_TINY in findings[0]["reason"]


def test_rule_off_page():
    data = make_pdf(lambda p: p.insert_text((-500, 100), "outside the page", fontsize=12, color=(0, 0, 0)))
    findings = detector.scan_pdf(data)
    assert len(findings) == 1
    assert detector.REASON_OFFPAGE in findings[0]["reason"]


def test_normal_text_is_clean():
    data = make_pdf(lambda p: p.insert_text((72, 100), "normal text", fontsize=12, color=(0, 0, 0)))
    assert detector.scan_pdf(data) == []


def test_neighbouring_flagged_spans_are_merged():
    def draw(page):
        first = "first part "
        page.insert_text((72, 100), first, fontsize=2, color=(0, 0, 0))
        x = 72 + fitz.get_text_length(first, fontsize=2)
        page.insert_text((x, 100), "second part", fontsize=3, color=(0, 0, 0))
    findings = detector.scan_pdf(make_pdf(draw))
    assert len(findings) == 1
    assert findings[0]["text"] == "first part second part"


# API error handling

def test_non_pdf_is_rejected():
    response = client.post("/api/scan", files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 400
