"""
Gemini Vision module for parsing grade sheet photos/PDFs.
Uses Google Gemini Vision API for extraction.
Designed for photographed paper grade sheets with table structure.

Handles:
- Printed tables with grid lines
- Mixed printed text and handwritten grade marks
- Column headers like EX1, A7, M1, D2, LO1, etc.
- Grade marks: checkmarks (✓), P, X, M, R, RQ, A, and similar symbols
"""

from typing import Dict, List, Optional
import os
import json
import re
import logging

logger = logging.getLogger(__name__)

try:
    import fitz  # type: ignore  # PyMuPDF for PDF→image conversion
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False


_EXTRACTION_PROMPT = """You are analyzing a photograph or scan of a paper grade sheet used in mastery-based grading.

The sheet is a table with:
- A header row containing column names. The first column is student names. The remaining columns are learning objective codes (like EX1, A7, M1, D2, LO1, LO2, etc.) or homework score columns (like HW, HW%, Score).
- Each subsequent row is a student. The first cell is the student's full name. The remaining cells contain grade marks.

Valid grade marks are:
- "M" = Mastered
- "P" = Progressing
- "X" = Not yet / incorrect
- "R" = Redo / Retake
- "RQ" = Redo Required
- "A" = Absent
- A checkmark (✓ or similar) should be interpreted as "P" (Progressing/Pass)
- A blank or empty cell should be omitted (do not include it)
- A number (like 85, 92, etc.) is a homework percentage score — include it as-is

Extract ALL students and ALL columns from the sheet. Be thorough — do not skip any rows or columns.

Return ONLY valid JSON in this exact format (no markdown fencing, no extra text):
{
  "learning_objectives": ["EX1", "A7", "M1", "D2"],
  "students": [
    {
      "name": "John Smith",
      "grades": {"EX1": "M", "A7": "P", "M1": "X", "D2": "R"}
    }
  ]
}

Rules:
- "learning_objectives" is the ordered list of column headers (excluding the student name column).
- Each student's "grades" object uses the column header as key and the grade mark as value.
- Omit empty/blank cells from the grades object entirely.
- Normalize all grade marks to uppercase (M, P, X, R, RQ, A).
- Convert any checkmark symbol to "P".
- Preserve numbers as strings (e.g., "85" not 85).
- Student names should be in their original order as they appear on the sheet.
- Preserve the exact spelling of student names as printed on the sheet.
"""


class GradeSheetGeminiAnalyzer:
    """
    Analyzes grade sheet photos/PDFs using Google Gemini Vision API.
    Sends images to Gemini and receives structured JSON with student grades.
    """

    def __init__(self):
        """Initialize the analyzer with Gemini client."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")
        if not HAS_GENAI:
            raise ImportError("google-genai not installed. Run: pip install google-genai")
        self.client = genai.Client(api_key=api_key)
        self.models = ["gemini-2.5-flash"]
    
    def analyze_pdf(self, file_obj) -> Dict:
        """
        Analyze a grade sheet file (PDF or image) and extract structured data.

        Args:
            file_obj: File bytes or file-like object (PDF, JPG, PNG)

        Returns:
            Dict with keys: students, learning_objectives, raw_text, success
        """
        try:
            import time as _time
            _t0 = _time.time()

            if hasattr(file_obj, 'read'):
                file_bytes = file_obj.read()
            else:
                file_bytes = file_obj

            logger.debug("[Gemini] analyze_pdf called, %s bytes", len(file_bytes))

            # Convert file to image parts for Gemini
            image_parts = self._file_to_image_parts(file_bytes)
            logger.debug(
                "[Gemini] Prepared %s image(s) in %.1fs",
                len(image_parts),
                _time.time() - _t0,
            )

            if not image_parts:
                raise Exception("Could not extract images from file")

            # Send to Gemini for extraction
            extracted = self._call_gemini(image_parts)
            logger.debug("[Gemini] Extraction done in %.1fs", _time.time() - _t0)
            logger.debug(
                "[Gemini] Found %s students, %s LOs",
                len(extracted["students"]),
                len(extracted["learning_objectives"]),
            )

            # Build raw text for display
            raw_lines = []
            headers = extracted['learning_objectives']
            if headers:
                raw_lines.append('Name | ' + ' | '.join(headers))
            for s in extracted['students']:
                grade_str = ' | '.join(
                    str(s['grades'].get(h, '')) for h in headers
                )
                raw_lines.append(f"{s['name']} | {grade_str}")

            return {
                'students': extracted['students'],
                'learning_objectives': extracted['learning_objectives'],
                'raw_text': '\n'.join(raw_lines),
                'success': True
            }

        except Exception as e:
            logger.exception("Gemini analyze_pdf failed")
            raise Exception(f"Error analyzing file: {str(e)}")

    def _file_to_image_parts(self, file_bytes: bytes) -> List:
        """Convert PDF or image file bytes into Gemini Part objects."""
        parts = []

        if file_bytes[:4] == b'%PDF':
            if not HAS_PYMUPDF:
                raise ImportError(
                    "PyMuPDF is required for PDF files. Run: pip install pymupdf")
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page in doc:
                mat = fitz.Matrix(2, 2)  # 2x zoom for better quality
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                parts.append(types.Part.from_bytes(
                    data=img_bytes,
                    mime_type="image/png"
                ))
            doc.close()
        else:
            mime_type = self._detect_mime_type(file_bytes)
            parts.append(types.Part.from_bytes(
                data=file_bytes,
                mime_type=mime_type
            ))

        return parts

    def _detect_mime_type(self, file_bytes: bytes) -> str:
        """Detect image MIME type from file header bytes."""
        if file_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        elif file_bytes[:2] == b'\xff\xd8':
            return "image/jpeg"
        elif file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WEBP':
            return "image/webp"
        return "image/jpeg"

    def _call_gemini(self, image_parts: List) -> Dict:
        """Send image(s) to Gemini and parse the structured JSON response.
        Tries each model in self.models, falling back on any error."""
        contents = image_parts + [_EXTRACTION_PROMPT]
        last_error = None

        model = self.models[0]
        try:
            logger.debug("[Gemini] Trying model: %s", model)
            response = self.client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                )
            )
            response_text = response.text.strip()
            parsed = self._parse_response(response_text)
            logger.debug("[Gemini] Success with model: %s", model)
            return self._normalize_data(parsed)
        except Exception as e:
            error_str = str(e)
            logger.warning("[Gemini] Model %s failed: %s", model, error_str)
            if '503' in error_str or 'UNAVAILABLE' in error_str:
                raise Exception("The Gemini AI service is temporarily busy. Please try again in a few minutes.")
            raise Exception(f"Gemini API error: {error_str}")

    def _parse_response(self, response_text: str) -> Dict:
        """Parse the Gemini response text into a dict."""
        text = response_text
        if text.startswith("```"):
            lines = text.split('\n')
            start = 1
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == '```':
                    end = i
                    break
            text = '\n'.join(lines[start:end])

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("[Gemini] Failed to parse response: %s", e)
            logger.debug("[Gemini] Response text snippet: %s", response_text[:500])
            raise Exception("Failed to parse grade sheet data from AI response")

    def _normalize_data(self, data: Dict) -> Dict:
        """Normalize and validate the extracted data."""
        learning_objectives = data.get('learning_objectives', [])
        students_raw = data.get('students', [])

        valid_marks = {'M', 'X', 'R', 'P', 'A', 'RQ', '/'}

        students = []
        for s in students_raw:
            name = (s.get('name') or '').strip()
            if not name or len(name) < 2:
                continue

            grades = {}
            raw_grades = s.get('grades', {})
            for lo, mark in raw_grades.items():
                mark_str = str(mark).strip().upper()
                if mark_str in ('✓', '✔', 'CHECK', 'PASS', 'YES'):
                    mark_str = 'P'
                if mark_str in valid_marks or re.match(r'^\d+\.?\d*$', mark_str):
                    grades[lo] = mark_str

            students.append({
                'name': name,
                'grades': grades
            })

        return {
            'learning_objectives': learning_objectives,
            'students': students
        }


_cached_analyzer = None
_module_version = 12  # Bumped for Gemini migration

def get_gemini_analyzer() -> Optional[GradeSheetGeminiAnalyzer]:
    """
    Factory function to create a Gemini grade sheet analyzer.
    Caches the instance so Gemini client is reused across requests.
    """
    global _cached_analyzer
    if (_cached_analyzer is not None
            and getattr(_cached_analyzer, '_version', 0) == _module_version):
        return _cached_analyzer
    try:
        _cached_analyzer = GradeSheetGeminiAnalyzer()
        _cached_analyzer._version = _module_version  # type: ignore
        return _cached_analyzer
    except (ImportError, ValueError) as e:
        logger.warning("Failed to initialize Gemini analyzer: %s", e)
        return None
