"""
OCR module for parsing grade sheet photos/PDFs.
Uses PyMuPDF and easyocr for extraction.
Designed for photographed paper grade sheets with table structure.

Handles:
- Printed tables with grid lines
- Mixed printed text and handwritten grade marks
- Column headers like EX1, A7, M1, D2, LO1, etc.
- Grade marks: checkmarks (✓), P, X, M, and similar symbols
"""

from typing import Dict, List, Optional, Tuple
import io
import re

try:
    import fitz  # type: ignore  # PyMuPDF for PDF handling
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import easyocr  # type: ignore
    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False


class GradeSheetOCRAnalyzer:
    """
    Analyzes grade sheet photos/PDFs using PyMuPDF + easyocr.
    Uses spatial analysis of OCR bounding boxes to reconstruct table structure.
    """
    
    def __init__(self):
        """Initialize the analyzer."""
        if not HAS_PYMUPDF:
            raise ImportError("PyMuPDF not installed. Run: pip install pymupdf")
        if not HAS_EASYOCR:
            print("Warning: easyocr not installed. Run: pip install easyocr")
        else:
            self.reader = easyocr.Reader(['en'])
        self.objective_order = []
    
    def analyze_pdf(self, file_obj) -> Dict:
        """
        Analyze a grade sheet file (PDF or JPG) and extract structured data.
        """
        try:
            import time as _time
            _t0 = _time.time()
            
            if hasattr(file_obj, 'read'):
                file_bytes = file_obj.read()
            else:
                file_bytes = file_obj
            
            print(f"[OCR] analyze_pdf called, {len(file_bytes)} bytes")
            
            # For text-based PDFs, try direct text extraction first
            if file_bytes[:4] == b'%PDF':
                extracted_text = self._try_direct_pdf_text(file_bytes)
                if extracted_text:
                    print(f"[OCR] Direct PDF text: {len(extracted_text)} lines in {_time.time()-_t0:.1f}s")
                    students_data = self._parse_text_lines(extracted_text)
                    return {
                        'students': students_data['students'],
                        'learning_objectives': self.objective_order,
                        'raw_text': '\n'.join(extracted_text),
                        'success': True
                    }
            
            # For images or scanned PDFs, use OCR with table-aware extraction
            print(f"[OCR] Using OCR table extraction...")
            images = self._file_to_images(file_bytes)
            print(f"[OCR] Got {len(images)} images in {_time.time()-_t0:.1f}s")
            
            if not images:
                raise Exception("Could not extract images from file")
            
            # Run table-aware OCR extraction
            table_data = self._extract_table_from_images(images)
            print(f"[OCR] Table extraction done in {_time.time()-_t0:.1f}s")
            print(f"[OCR] Found {len(table_data['students'])} students, headers: {table_data['headers']}")
            
            self.objective_order = table_data['headers']
            
            # Build raw text for display
            raw_lines = []
            if table_data['headers']:
                raw_lines.append('Name | ' + ' | '.join(table_data['headers']))
            for s in table_data['students']:
                grade_str = ' | '.join(str(s['grades'].get(h, '')) for h in table_data['headers'])
                raw_lines.append(f"{s['name']} | {grade_str}")
            
            return {
                'students': table_data['students'],
                'learning_objectives': table_data['headers'],
                'raw_text': '\n'.join(raw_lines),
                'success': True
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise Exception(f"Error analyzing file: {str(e)}")
    
    def _try_direct_pdf_text(self, file_bytes: bytes) -> Optional[List[str]]:
        """Try to extract text directly from a text-based PDF. Returns None if insufficient."""
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        lines = []
        for page in doc:
            page_text = page.get_text()
            if page_text.strip():
                lines.extend(page_text.split('\n'))
        doc.close()
        
        if len(''.join(lines)) > 50:
            return lines
        return None
    
    def _file_to_images(self, file_bytes: bytes) -> List:
        """Convert PDF or image file to PIL Image objects."""
        from PIL import Image, ImageOps  # type: ignore
        
        images = []
        if file_bytes[:4] == b'%PDF':
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page in doc:
                mat = fitz.Matrix(2, 2)  # 2x zoom for better OCR
                pix = page.get_pixmap(matrix=mat)
                img = Image.open(io.BytesIO(pix.tobytes()))
                images.append(img)
            doc.close()
        else:
            image = Image.open(io.BytesIO(file_bytes))
            # Fix phone photo rotation using EXIF orientation data
            image = ImageOps.exif_transpose(image)
            images = [image]
        
        return images
    
    def _preprocess_for_ocr(self, image):
        """
        Preprocessing for photos of paper grade sheets.
        Handles both small and large images appropriately.
        """
        from PIL import Image, ImageEnhance, ImageFilter  # type: ignore
        
        # For very large images (phone photos), downscale to reasonable size
        # easyocr works best around 1500-2500px wide
        max_width = 2500
        if image.width > max_width:
            scale = max_width / image.width
            image = image.resize(
                (int(image.width * scale), int(image.height * scale)),
                Image.LANCZOS
            )
            print(f"[OCR] Downscaled to {image.width}x{image.height}")
        
        # For small images, upscale
        min_width = 1500
        if image.width < min_width:
            scale = min_width / image.width
            image = image.resize(
                (int(image.width * scale), int(image.height * scale)),
                Image.LANCZOS
            )
            print(f"[OCR] Upscaled to {image.width}x{image.height}")
        
        # Convert to grayscale
        gray = image.convert('L')
        
        # Contrast boost and sharpen for paper photos
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(1.5)
        gray = gray.filter(ImageFilter.SHARPEN)
        
        return gray.convert('RGB')
    
    def _extract_table_from_images(self, images: List) -> Dict:
        """
        Extract structured table data from grade sheet images.
        Uses spatial analysis of OCR bounding boxes to reconstruct the table.
        Two-pass approach: first for names/scores, second for handwritten marks.
        
        Returns dict with 'headers' (list of column names) and 'students' (list of dicts).
        """
        if not HAS_EASYOCR:
            raise ImportError("easyocr not installed. Run: pip install easyocr")
        
        import numpy as np  # type: ignore
        
        all_students = []
        all_headers = []
        
        for image in images:
            # Preprocess
            processed = self._preprocess_for_ocr(image)
            img_array = np.array(processed)
            
            # === PASS 1: Normal OCR for names, scores, and table structure ===
            print(f"[OCR] Pass 1: Running easyocr on {img_array.shape} image...")
            results = self.reader.readtext(img_array)
            print(f"[OCR] Pass 1: Got {len(results)} text regions")
            
            if not results:
                continue
            
            # Collect all regions with position info
            regions = []
            for (bbox, text, conf) in results:
                if conf > 0.15 and text.strip():
                    x_left = min(pt[0] for pt in bbox)
                    x_right = max(pt[0] for pt in bbox)
                    y_top = min(pt[1] for pt in bbox)
                    y_bottom = max(pt[1] for pt in bbox)
                    y_center = (y_top + y_bottom) / 2
                    x_center = (x_left + x_right) / 2
                    regions.append({
                        'text': text.strip(),
                        'conf': conf,
                        'x_left': x_left, 'x_right': x_right, 'x_center': x_center,
                        'y_top': y_top, 'y_bottom': y_bottom, 'y_center': y_center,
                        'width': x_right - x_left,
                        'height': y_bottom - y_top,
                    })
            
            if not regions:
                continue
            
            # Group regions into rows by y-position
            rows = self._group_into_rows(regions)
            print(f"[OCR] Grouped into {len(rows)} rows")
            
            # Debug: print first few rows
            for i, row in enumerate(rows[:5]):
                texts = [r['text'] for r in sorted(row, key=lambda r: r['x_left'])]
                print(f"[OCR]   Row {i}: {texts}")
            
            # Identify header row and extract column structure
            header_row_idx, headers, col_positions = self._find_header_row(rows)
            print(f"[OCR] Header at row {header_row_idx}: {headers}")
            print(f"[OCR] Column positions: {[(h, round(x)) for h, x in col_positions]}")
            
            if headers:
                all_headers = headers
            
            # Extract student data from rows after header, tracking y-positions
            data_rows = rows[header_row_idx + 1:] if header_row_idx >= 0 else rows
            student_y_positions = []  # Track y-center for each student for pass 2
            
            for row_idx, row in enumerate(data_rows):
                student = self._extract_student_from_row(row, col_positions, headers)
                if student:
                    # Record the y-center of this row for marks mapping
                    row_y = sum(r['y_center'] for r in row) / len(row)
                    student_y_positions.append(row_y)
                    all_students.append(student)
                else:
                    if row_idx < 5:
                        texts = [r['text'] for r in sorted(row, key=lambda r: r['x_left'])]
                        print(f"[OCR]   Skipped row {row_idx}: {texts[:6]}")
            
            # === PASS 2: Targeted OCR for handwritten marks ===
            mark_col_positions = [(h, x) for h, x in col_positions
                                  if not re.match(r'^(EX\d+|FEX)$', h, re.IGNORECASE)]
            
            if mark_col_positions and student_y_positions and all_students:
                print(f"[OCR] Pass 2: Detecting handwritten marks in {len(mark_col_positions)} columns...")
                marks = self._detect_marks_second_pass(
                    image, mark_col_positions, student_y_positions, col_positions
                )
                # Merge marks into student data
                marks_added = 0
                for student_idx, col_name, mark_value in marks:
                    if 0 <= student_idx < len(all_students):
                        # Only add if we don't already have a value
                        if col_name not in all_students[student_idx]['grades'] or \
                           not all_students[student_idx]['grades'][col_name]:
                            all_students[student_idx]['grades'][col_name] = mark_value
                            marks_added += 1
                print(f"[OCR] Pass 2: Added {marks_added} marks")
        
        return {
            'headers': all_headers,
            'students': all_students
        }
    
    def _detect_marks_second_pass(self, original_image, mark_col_positions, 
                                   student_y_positions, all_col_positions) -> List[Tuple[int, str, str]]:
        """
        Second OCR pass to detect handwritten marks (P, X, M) in mark columns.
        Crops the marks area, upscales, and runs OCR with character allowlist.
        
        Returns list of (student_index, column_name, mark_value) tuples.
        """
        import numpy as np  # type: ignore
        from PIL import Image, ImageEnhance, ImageFilter  # type: ignore
        
        # Determine the crop region for marks area
        first_mark_x = min(x for _, x in mark_col_positions)
        crop_x_start = max(0, int(first_mark_x - 40))  # Small margin left of first mark column
        
        # Header y is above first student; crop from above first student to below last
        header_y = min(student_y_positions) - 40 if student_y_positions else 0
        crop_y_start = max(0, int(header_y))
        
        # Crop marks area from original image
        marks_crop = original_image.crop((crop_x_start, crop_y_start, 
                                          original_image.width, original_image.height))
        
        # Upscale 3x for better mark detection
        scale_factor = 3
        w, h = marks_crop.size
        marks_up = marks_crop.resize((w * scale_factor, h * scale_factor), Image.LANCZOS)
        
        # Convert to grayscale and binarize for handwriting
        marks_gray = marks_up.convert('L')
        enhancer = ImageEnhance.Contrast(marks_gray)
        marks_gray = enhancer.enhance(2.0)
        marks_gray = marks_gray.filter(ImageFilter.SHARPEN)
        marks_gray = marks_gray.filter(ImageFilter.SHARPEN)
        # Binarize
        threshold = 160
        marks_bw = marks_gray.point(lambda p: 255 if p > threshold else 0)
        
        arr_marks = np.array(marks_bw.convert('RGB'))
        print(f"[OCR] Pass 2: Marks region {arr_marks.shape}, scale={scale_factor}x")
        
        # Run OCR with character allowlist and lower thresholds
        results = self.reader.readtext(
            arr_marks,
            allowlist='PXMpxmRr',
            text_threshold=0.2,
            low_text=0.2,
        )
        print(f"[OCR] Pass 2: Got {len(results)} mark detections")
        
        # Map each detection back to (student, column) using coordinates
        detected_marks = []
        for (bbox, text, conf) in results:
            if not text.strip():
                continue
            
            # Convert coordinates back to original image space
            x_center = (min(pt[0] for pt in bbox) + max(pt[0] for pt in bbox)) / 2
            y_center = (min(pt[1] for pt in bbox) + max(pt[1] for pt in bbox)) / 2
            
            orig_x = x_center / scale_factor + crop_x_start
            orig_y = y_center / scale_factor + crop_y_start
            
            # Find nearest mark column
            best_col = None
            best_col_dist = float('inf')
            for col_name, col_x in mark_col_positions:
                dist = abs(orig_x - col_x)
                if dist < best_col_dist:
                    best_col_dist = dist
                    best_col = col_name
            
            # Find nearest student row
            best_student = None
            best_row_dist = float('inf')
            for idx, row_y in enumerate(student_y_positions):
                dist = abs(orig_y - row_y)
                if dist < best_row_dist:
                    best_row_dist = dist
                    best_student = idx
            
            # Must be reasonably close to a column and row
            if best_col is None or best_student is None:
                continue
            if best_col_dist > 40 or best_row_dist > 25:
                continue
            
            # Normalize the mark character
            mark = text.strip().upper()
            # Take just the first character if multiple were merged
            if len(mark) > 1:
                # If it contains both valid marks, take each one per column (skip merged)
                # For now, just take the first character
                mark = mark[0]
            
            if mark in ('P', 'X', 'M', 'R'):
                detected_marks.append((best_student, best_col, mark))
        
        return detected_marks
    
    def _group_into_rows(self, regions: List[Dict]) -> List[List[Dict]]:
        """Group text regions into rows based on y-position."""
        if not regions:
            return []
        
        # Sort by y position
        regions.sort(key=lambda r: r['y_center'])
        
        # Calculate row grouping threshold from median text height
        heights = sorted([r['height'] for r in regions])
        median_height = heights[len(heights) // 2] if heights else 20
        row_threshold = median_height * 0.5
        
        rows = []
        current_row = [regions[0]]
        
        for region in regions[1:]:
            # Check if this region belongs to the current row
            current_y = sum(r['y_center'] for r in current_row) / len(current_row)
            if abs(region['y_center'] - current_y) < row_threshold:
                current_row.append(region)
            else:
                rows.append(current_row)
                current_row = [region]
        rows.append(current_row)
        
        # Sort each row by x-position
        for row in rows:
            row.sort(key=lambda r: r['x_left'])
        
        return rows
    
    def _find_header_row(self, rows: List[List[Dict]]) -> Tuple[int, List[str], List[Tuple[str, float]]]:
        """
        Find the header row in the table and extract column positions.
        
        Returns:
            (header_row_index, header_names, column_positions)
            column_positions is a list of (header_name, x_center) tuples
        """
        # Known assignment/column header patterns
        header_patterns = [
            r'^[A-Z]{1,3}\d{1,2}$',   # A7, M1, D2, EX1, LO1, etc.
            r'^FEX$',                   # Final exam
            r'^EX\d+$',                # Exam columns
            r'^D\d+$',                 # D columns
            r'^A\d+$',                 # Assignment columns
            r'^M\d+$',                 # M columns
            r'^\d+\.\d+$',            # Numeric like 1.1
        ]
        
        best_row_idx = -1
        best_score = 0
        
        # Check first ~8 rows for a header
        for i, row in enumerate(rows[:8]):
            texts = [r['text'].strip() for r in row]
            score = 0
            for text in texts:
                # Split merged text like "A7 IA3 IA4" into parts
                parts = re.split(r'[\s|]+', text)
                for part in parts:
                    # Strip leading I/| from grid lines (e.g. "IA9" -> "A9")
                    cleaned = re.sub(r'^[I|]+(?=[A-Z]\d)', '', part)
                    for pattern in header_patterns:
                        if re.match(pattern, cleaned, re.IGNORECASE):
                            score += 1
                            break
                # Also check for common header words
                if text.lower() in ('student', 'name', 'points', 'possible'):
                    score += 0.5
            
            if score > best_score:
                best_score = score
                best_row_idx = i
        
        if best_row_idx < 0 or best_score < 2:
            # No clear header found - try to use first row
            best_row_idx = 0
        
        # Extract headers and their x-positions
        header_row = rows[best_row_idx]
        headers = []
        col_positions = []
        
        for region in header_row:
            text = region['text'].strip()
            # Skip "Student", "Name", "Points Possible", numbers-only
            if text.lower() in ('student', 'name', 'points', 'possible', 'points possible'):
                continue
            if re.match(r'^\d+$', text):
                continue
            
            # Handle merged headers like "A7   IA3  IA4" — split and assign positions
            # easyocr sometimes merges adjacent column headers into one region
            parts = re.split(r'[\s|]+', text)
            if len(parts) > 1:
                # Multiple headers merged — distribute x-positions across the region width
                region_width = region['x_right'] - region['x_left']
                for j, part in enumerate(parts):
                    cleaned = self._clean_header_text(part)
                    if cleaned:
                        # Evenly space the sub-headers across the region
                        sub_x = region['x_left'] + (j + 0.5) * region_width / len(parts)
                        headers.append(cleaned)
                        col_positions.append((cleaned, sub_x))
            else:
                cleaned = self._clean_header_text(text)
                if cleaned:
                    headers.append(cleaned)
                    col_positions.append((cleaned, region['x_center']))
        
        return best_row_idx, headers, col_positions
    
    def _clean_header_text(self, text: str) -> Optional[str]:
        """Clean a single header text: strip leading I/| artifacts from grid lines."""
        t = text.strip()
        if not t:
            return None
        # Strip leading I or | that easyocr picks up from table grid lines
        # e.g. "IA9" -> "A9", "IM2" -> "M2", "|A3" -> "A3"
        t = re.sub(r'^[I|]+(?=[A-Z]\d)', '', t)
        # Validate it looks like a column header
        if re.match(r'^[A-Z]{1,3}\d{1,2}$', t, re.IGNORECASE) or t.upper() == 'FEX':
            return t
        return None
    
    def _extract_student_from_row(self, row: List[Dict], col_positions: List[Tuple[str, float]], 
                                   headers: List[str]) -> Optional[Dict]:
        """
        Extract student name and grades from a data row.
        Uses column x-positions from header to map grades to the right columns.
        """
        if not row:
            return None
        
        # Separate the row into: name region (leftmost text) and grade regions
        name_parts = []
        grade_regions = []
        
        if col_positions:
            first_col_x = col_positions[0][1]
            # Name boundary: anything to the left of the first data column header
            # Use the left edge of first column with generous margin
            name_boundary = first_col_x - 20  # 20px margin to the left of first header center
            
            for region in row:
                if region['x_center'] < name_boundary:
                    name_parts.append(region)
                else:
                    grade_regions.append(region)
        else:
            if len(row) > 0:
                name_parts = [row[0]]
                grade_regions = row[1:]
        
        # Build student name from name parts
        name_texts = [r['text'] for r in sorted(name_parts, key=lambda r: r['x_left'])]
        
        # Filter out row numbers and numeric scores
        filtered_name = []
        for t in name_texts:
            t_stripped = t.strip()
            if re.match(r'^\d+$', t_stripped):
                continue
            if re.match(r'^\d+\.?\d*$', t_stripped):
                continue
            filtered_name.append(t_stripped)
        
        name = ' '.join(filtered_name).strip()
        
        # Clean up name: remove leading/trailing punctuation, fix common OCR artifacts
        name = re.sub(r'^[^a-zA-Z]+', '', name)  # Remove leading non-letters
        name = re.sub(r'[_|]+$', '', name)  # Remove trailing underscores/pipes
        name = name.replace('_', ' ')  # Underscores to spaces
        name = name.replace(';', ',')  # Semicolons to commas (OCR misread)
        name = name.strip(' .,;:')
        
        # Strip leading lowercase chars before a capital (OCR artifact from row numbers)
        # e.g. "eJBumanglag" -> "Bumanglag" (the "6" was misread as "eJ")
        name = re.sub(r'^[a-z]{1,3}(?=[A-Z])', '', name)
        # Also strip single uppercase letter stuck before another uppercase start
        # e.g. "JBumanglag" -> "Bumanglag" (residual from row number misread)
        name = re.sub(r'^[A-Z](?=[A-Z][a-z])', '', name)
        
        # Split CamelCase names that got merged (e.g. "MichelsSophia" -> "Michels, Sophia")
        # Pattern: LastnameFirstname where both start with uppercase
        camel = re.match(r'^([A-Z][a-z]+)([A-Z][a-z]+)$', name)
        if camel:
            name = f"{camel.group(1)}, {camel.group(2)}"
        # Also handle "TooleMarisa" pattern with longer names
        camel2 = re.match(r'^([A-Z][a-z]+)([A-Z][a-z]+.*)$', name)
        if camel2 and ',' not in name and ' ' not in name:
            name = f"{camel2.group(1)}, {camel2.group(2)}"
        
        if not name or len(name) < 2:
            return None
        
        # Don't treat header-like text as names
        if name.lower() in ('student', 'name', 'points possible', 'math', 'student name',
                            'points', 'possible'):
            return None
        # Skip if name contains "points possible" anywhere
        if 'points' in name.lower() and 'possible' in name.lower():
            return None
        # Skip class title rows like "MATH 130 12-16-25"
        if re.match(r'^MATH\s+\d', name, re.IGNORECASE):
            return None
        
        # Map grade regions to columns using x-position proximity
        grades = {}
        for region in grade_regions:
            # Find the closest column header by x-position
            best_header = None
            best_dist = float('inf')
            for header_name, header_x in col_positions:
                dist = abs(region['x_center'] - header_x)
                if dist < best_dist:
                    best_dist = dist
                    best_header = header_name
            
            if best_header and best_dist < 80:  # Must be reasonably close to a column
                text = region['text'].strip()
                # Determine if this is a numeric column (EX, FEX) or a mark column (A, M, D, LO)
                is_numeric_col = bool(re.match(r'^(EX\d+|FEX)$', best_header, re.IGNORECASE))
                text = self._normalize_grade_mark(text, is_numeric_col)
                if text:
                    grades[best_header] = text
        
        return {
            'name': name,
            'grades': grades
        }
    
    def _normalize_grade_mark(self, text: str, is_numeric_col: bool = False) -> str:
        """
        Normalize OCR-read grade marks to standard symbols.
        
        Args:
            text: Raw OCR text
            is_numeric_col: True for EX/FEX columns (expect numbers), 
                           False for A/M/D/LO columns (expect P/X/M marks)
        """
        t = text.strip().rstrip('|]}')
        
        # Dash/hyphen means empty — no grade
        if t in ('-', '—', '_', '.'):
            return ''
        
        if is_numeric_col:
            # Numeric columns: extract the number, discard non-numeric garbage
            cleaned = re.sub(r'[^\d.]', '', t)
            if cleaned and re.match(r'^\d+\.?\d*$', cleaned):
                # Remove trailing dots
                cleaned = cleaned.rstrip('.')
                # Cap decimal places at 1 (scores like 79.61 -> 79.6)
                if '.' in cleaned:
                    parts = cleaned.split('.')
                    cleaned = parts[0] + '.' + parts[1][:1]
                # Scores should be <= 200; if parsing gives a huge number, trim trailing digit
                try:
                    val = float(cleaned)
                    if val > 200 and len(cleaned.replace('.', '')) > 2:
                        # Re-derive from original without last digit
                        cleaned_orig = re.sub(r'[^\d.]', '', t).rstrip('.')
                        cleaned = cleaned_orig[:-1]
                        if '.' in cleaned:
                            parts = cleaned.split('.')
                            cleaned = parts[0] + '.' + parts[1][:1]
                except ValueError:
                    pass
                return cleaned
            return ''
        else:
            # Mark columns (A, M, D, LO): expect P, X, M, or checkmark
            t_upper = t.upper().strip()
            
            # Valid single-letter grade marks
            if t_upper in ('P', 'M', 'X', 'R'):
                return t_upper
            
            # Common OCR misreadings of checkmarks / P
            if t in ('V', 'v', '\u221a', '~', 'J', 'j', ')', '}', '/', '|'):
                return 'P'
            
            # A pure number in a mark column is likely garbage from an adjacent cell
            if re.match(r'^\d+\.?\d*$', t):
                return ''
            
            # Everything else in a mark column is OCR garbage — discard
            return ''
    
    def _parse_text_lines(self, text_list: List[str]) -> Dict:
        """
        Parse grade sheet data from direct text extraction (text-based PDFs).
        Falls back to the line-based parser for non-image sources.
        """
        students = []
        lines = text_list
        
        for line in lines:
            if not line.strip() or len(line) < 3:
                continue
            
            line_clean = line.strip()
            
            if self._is_header_line(line_clean):
                continue
            
            student_data = self._extract_student_from_text_line(line_clean)
            if student_data:
                students.append(student_data)
        
        return {
            'students': students,
            'learning_objectives': self.objective_order
        }
    
    def _is_header_line(self, line: str) -> bool:
        """Check if line is likely a header line."""
        if len(line) < 3:
            return False
        
        line_lower = line.lower()
        
        header_keywords = ['objective', 'date', 'class', 'student', 'points possible']
        if any(kw in line_lower for kw in header_keywords):
            objectives = self._extract_objectives_from_header(line)
            if objectives:
                self.objective_order = objectives
            return True
        
        if line_lower.startswith('name'):
            objectives = self._extract_objectives_from_header(line)
            if objectives:
                self.objective_order = objectives
            return True
        
        objectives = self._extract_objectives_from_header(line)
        if len(objectives) >= 2:
            words = line.split()
            if len(objectives) >= len(words) * 0.5:
                self.objective_order = objectives
                return True
        
        return False
    
    def _extract_objectives_from_header(self, header: str) -> List[str]:
        """Extract learning objective / assignment codes from header line."""
        patterns = [
            r'\d+\.\d+',
            r'LO\d+',
            r'OBJ\d+',
            r'EX\d+',
            r'FEX',
            r'[ADMQ]\d+',
        ]
        
        objectives = []
        seen = set()
        for pattern in patterns:
            matches = re.findall(pattern, header, re.IGNORECASE)
            for m in matches:
                m_upper = m.upper()
                if m_upper not in seen:
                    seen.add(m_upper)
                    objectives.append(m_upper)
        
        return objectives
    
    def _extract_student_from_text_line(self, line: str) -> Optional[Dict]:
        """Extract student name and grades from a text line."""
        parts = re.split(r'[|,\s]+', line.strip())
        
        if len(parts) < 1:
            return None
        
        grade_marks = {'M', 'X', 'R', 'P', 'A', '/', 'RQ'}
        
        name_parts = []
        grade_start = len(parts)
        
        for i, part in enumerate(parts):
            if part.strip().upper() in grade_marks and i >= 1:
                grade_start = i
                break
            name_parts.append(part)
        
        if not name_parts:
            return None
        
        potential_name = ' '.join(name_parts)
        
        if len(potential_name) < 2 or not re.search(r'[a-zA-Z]', potential_name):
            return None
        
        # Don't accept all-grade-mark "names"
        if all(w.upper() in grade_marks for w in potential_name.split()):
            return None
        
        grade_parts = parts[grade_start:]
        grades = {}
        for i, part in enumerate(grade_parts):
            part_clean = part.strip().upper()
            if part_clean in grade_marks or re.match(r'^[A-Z/]{1,2}$', part_clean):
                lo = self.objective_order[i] if i < len(self.objective_order) else f'LO{i+1}'
                grades[lo] = part_clean
        
        return {'name': potential_name, 'grades': grades}


_cached_analyzer = None
_module_version = 10  # Bump this to force re-creation of cached analyzer

def get_ocr_analyzer() -> Optional[GradeSheetOCRAnalyzer]:
    """
    Factory function to create an OCR analyzer.
    Caches the instance so easyocr Reader is only initialized once.
    """
    global _cached_analyzer
    if _cached_analyzer is not None and getattr(_cached_analyzer, '_version', 0) == _module_version:
        return _cached_analyzer
    try:
        _cached_analyzer = GradeSheetOCRAnalyzer()
        _cached_analyzer._version = _module_version
        return _cached_analyzer
    except ImportError as e:
        print(f"Failed to initialize OCR analyzer: {e}")
        return None
