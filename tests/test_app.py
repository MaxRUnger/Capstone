"""
Test suite for the Capstone grading application.

Covers:
    - Grade model logic (normalize_score, is_mastered, get_priority)
    - Route helper functions (enrich_grade, organize_by_learning_objectives, normalize_profile)
    - Flask app factory / configuration
    - Route smoke tests (public pages, auth redirects)
"""
import sys
import os
import unittest
import unittest.mock
from unittest.mock import MagicMock

_mock_supabase = MagicMock()
_mock_supabase_admin = MagicMock()

sys.modules.setdefault('app.authentication', MagicMock(
    supabase=_mock_supabase,
    supabase_admin=_mock_supabase_admin,
))

from app.models import (
    Course,
    Grade,
    Homework,
    MASTERY_GRADES,
    ASSIGNMENT_TYPE_PROJECT,
)
from app.routes import (
    organize_by_learning_objectives,
    normalize_profile,
    DEFAULT_REQUIRED_MS,
    _student_row_sort_key,
    _student_sort_key_last_name,
    _format_name_last_first,
    _student_display_name,
    _aggregate_lo_grades,
)
from app import create_app


# ==========================================================================
# Grade Model Tests
# ==========================================================================

class TestGradeNormalizeScore(unittest.TestCase):
    """Tests for Grade.normalize_score — the core grading logic."""

    def test_none_returns_none(self):
        self.assertIsNone(Grade.normalize_score(None))

    # -- numeric conversions --
    def test_high_numeric_returns_M(self):
        self.assertEqual(Grade.normalize_score(95), 'M')
        self.assertEqual(Grade.normalize_score(70), 'M')

    def test_mid_numeric_returns_R(self):
        self.assertEqual(Grade.normalize_score(65), 'R')
        self.assertEqual(Grade.normalize_score(50), 'R')

    def test_low_numeric_returns_P(self):
        self.assertEqual(Grade.normalize_score(30), 'P')
        self.assertEqual(Grade.normalize_score(0), 'P')

    def test_numeric_string_treated_as_number(self):
        self.assertEqual(Grade.normalize_score('99'), 'M')
        self.assertEqual(Grade.normalize_score('55.5'), 'R')

    def test_float_boundary(self):
        self.assertEqual(Grade.normalize_score(69.9), 'R')
        self.assertEqual(Grade.normalize_score(70.0), 'M')

    # -- pass-through codes --
    def test_valid_codes_returned_as_is(self):
        for code in ('M', 'MR', 'R', 'RQ', 'P', 'X', 'A', 'I'):
            self.assertEqual(Grade.normalize_score(code), code)

    def test_codes_are_case_insensitive(self):
        self.assertEqual(Grade.normalize_score('m'), 'M')
        self.assertEqual(Grade.normalize_score('rq'), 'RQ')

    def test_whitespace_stripped(self):
        self.assertEqual(Grade.normalize_score('  M  '), 'M')

    def test_unrecognized_string_returns_none(self):
        self.assertIsNone(Grade.normalize_score('Z'))
        self.assertIsNone(Grade.normalize_score('hello'))


class TestGradeIsMastered(unittest.TestCase):
    """Tests for Grade.is_mastered — checks if both scores are 'M'."""

    def test_both_M_is_mastered(self):
        self.assertTrue(Grade.is_mastered('M', 'M'))

    def test_MR_counts_as_mastery_in_pair(self):
        self.assertTrue(Grade.is_mastered('MR', 'MR'))
        self.assertTrue(Grade.is_mastered('M', 'MR'))
        self.assertTrue(Grade.is_mastered('MR', 'M'))

    def test_only_top_M_not_mastered(self):
        self.assertFalse(Grade.is_mastered('M', 'R'))

    def test_only_second_M_not_mastered(self):
        self.assertFalse(Grade.is_mastered('R', 'M'))

    def test_neither_M_not_mastered(self):
        self.assertFalse(Grade.is_mastered('P', 'X'))


class TestGradeGetPriority(unittest.TestCase):
    """Tests for Grade.get_priority — used for score comparisons."""

    def test_known_grades_ordered_correctly(self):
        self.assertEqual(Grade.get_priority('M'), Grade.get_priority('MR'))
        self.assertGreater(Grade.get_priority('M'), Grade.get_priority('R'))
        self.assertGreater(Grade.get_priority('R'), Grade.get_priority('RQ'))
        self.assertGreater(Grade.get_priority('RQ'), Grade.get_priority('P'))
        self.assertGreater(Grade.get_priority('P'), Grade.get_priority('X'))
        self.assertGreater(Grade.get_priority('X'), Grade.get_priority('A'))
        self.assertEqual(Grade.get_priority('I'), Grade.get_priority('X'))

    def test_unknown_grade_returns_negative(self):
        self.assertEqual(Grade.get_priority('Z'), -1)
        self.assertEqual(Grade.get_priority(None), -1)


class TestHomeworkImportSheetColumn(unittest.TestCase):
    """Homework % columns on scanned sheets are not learning objectives."""

    def test_is_import_sheet_hw_column(self):
        self.assertTrue(Homework.is_import_sheet_hw_column("HW"))
        self.assertTrue(Homework.is_import_sheet_hw_column("  HW%  "))
        self.assertTrue(Homework.is_import_sheet_hw_column("Homework"))
        self.assertTrue(Homework.is_import_sheet_hw_column("HW1"))
        self.assertFalse(Homework.is_import_sheet_hw_column("EX1"))
        self.assertFalse(Homework.is_import_sheet_hw_column("A7"))

    def test_normalize_homework_group(self):
        # homework_group is a free-form grouping key: trim + collapse whitespace only.
        self.assertEqual(Homework.normalize_homework_group("Quiz 1"), "Quiz 1")
        self.assertEqual(Homework.normalize_homework_group("  Chapter  5   HW  "), "Chapter 5 HW")
        self.assertEqual(Homework.normalize_homework_group("EX1"), "EX1")
        self.assertIsNone(Homework.normalize_homework_group(""))
        self.assertIsNone(Homework.normalize_homework_group("   "))
        self.assertIsNone(Homework.normalize_homework_group(None))

    def test_canonicalize_homework_group_for_class_reuses_existing_casing(self):
        # If a differently-cased label already exists for this class, reuse
        # its exact casing so the two assignments share one HW group instead
        # of silently splitting into two ("Quiz 1" vs "quiz 1").
        with unittest.mock.patch.object(Homework, "normalize_homework_group", wraps=Homework.normalize_homework_group):
            mock_table = MagicMock()
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"homework_group": "Quiz 1"}, {"homework_group": "Unit 3"}]
            )
            with unittest.mock.patch("app.models.supabase_admin") as sa:
                sa.table.return_value = mock_table
                self.assertEqual(
                    Homework.canonicalize_homework_group_for_class("class-1", "quiz 1"),
                    "Quiz 1",
                )
                self.assertEqual(
                    Homework.canonicalize_homework_group_for_class("class-1", "  QUIZ   1  "),
                    "Quiz 1",
                )
                # A genuinely new label is returned as typed (trim/whitespace only).
                self.assertEqual(
                    Homework.canonicalize_homework_group_for_class("class-1", "Midterm"),
                    "Midterm",
                )

    def test_canonicalize_homework_group_for_class_handles_empty_and_lookup_errors(self):
        self.assertIsNone(Homework.canonicalize_homework_group_for_class("class-1", ""))
        self.assertIsNone(Homework.canonicalize_homework_group_for_class("class-1", None))
        with unittest.mock.patch("app.models.supabase_admin") as sa:
            sa.table.side_effect = Exception("boom")
            # Lookup failures shouldn't block assignment creation — fall back
            # to the normalized candidate as-is.
            self.assertEqual(
                Homework.canonicalize_homework_group_for_class("class-1", "Quiz 1"),
                "Quiz 1",
            )

    def test_parse_import_hw_pct(self):
        self.assertEqual(Homework.parse_import_hw_pct("85"), 85)
        self.assertEqual(Homework.parse_import_hw_pct(" 92.3% "), 92)
        self.assertEqual(Homework.parse_import_hw_pct(-1), -1)
        self.assertIsNone(Homework.parse_import_hw_pct("M"))
        self.assertIsNone(Homework.parse_import_hw_pct(""))
        self.assertIsNone(Homework.parse_import_hw_pct(None))


class TestHomeworkEligibilityThresholds(unittest.TestCase):
    """HW % rules: 65%+ in-class exam marks; 75%+ for M/MR; pass (-1) = exam only."""

    def test_exam_grade_eligible(self):
        self.assertFalse(Homework.is_exam_grade_eligible_hw_score(None))
        self.assertFalse(Homework.is_exam_grade_eligible_hw_score(64))
        self.assertTrue(Homework.is_exam_grade_eligible_hw_score(65))
        self.assertTrue(Homework.is_exam_grade_eligible_hw_score(100))
        self.assertTrue(Homework.is_exam_grade_eligible_hw_score(-1))

    def test_revision_to_m_eligible(self):
        self.assertFalse(Homework.is_revision_to_m_eligible_hw_score(None))
        self.assertFalse(Homework.is_revision_to_m_eligible_hw_score(-1))
        self.assertFalse(Homework.is_revision_to_m_eligible_hw_score(74))
        self.assertTrue(Homework.is_revision_to_m_eligible_hw_score(75))
        self.assertTrue(Homework.is_revision_to_m_eligible_hw_score(100))


# ==========================================================================
# Route Helper Tests
# ==========================================================================

class TestOrganizeByLearningObjectives(unittest.TestCase):
    """Tests for organize_by_learning_objectives — builds the LO summary."""

    def _build_data(self, grades_list):
        """Helper: single student with the given grades."""
        return [{'id': 'stu1', 'full_name': 'Test Student', 'grades': grades_list}]

    def test_student_with_2m(self):
        students = self._build_data([
            {'learning_objective_id': '10', 'top_score': 'M', 'second_score': 'M'},
        ])
        los = [{'id': '10', 'name': 'LO-A'}]
        result = organize_by_learning_objectives(students, los)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['students_with_2m']), 1)
        self.assertEqual(len(result[0]['students_with_1m']), 0)

    def test_student_with_2m_via_MR(self):
        students = self._build_data([
            {'learning_objective_id': '10', 'top_score': 'MR', 'second_score': 'MR'},
        ])
        los = [{'id': '10', 'name': 'LO-A'}]
        result = organize_by_learning_objectives(students, los)
        self.assertEqual(len(result[0]['students_with_2m']), 1)

    def test_student_with_1m(self):
        students = self._build_data([
            {'learning_objective_id': '10', 'top_score': 'M', 'second_score': 'R'},
        ])
        los = [{'id': '10', 'name': 'LO-A'}]
        result = organize_by_learning_objectives(students, los)
        self.assertEqual(len(result[0]['students_with_1m']), 1)

    def test_student_with_0m(self):
        students = self._build_data([
            {'learning_objective_id': '10', 'top_score': 'P', 'second_score': 'X'},
        ])
        los = [{'id': '10', 'name': 'LO-A'}]
        result = organize_by_learning_objectives(students, los)
        self.assertEqual(len(result[0]['students_with_0m']), 1)

    def test_empty_students_list(self):
        los = [{'id': '10', 'name': 'LO-A'}]
        result = organize_by_learning_objectives([], los)
        self.assertEqual(result[0]['total_students'], 0)
        self.assertEqual(result[0]['students_with_2m'], [])

    def test_aggregate_lo_counts_M_and_MR(self):
        lo_lookup = {'1': {'id': '1', 'name': 'LO', 'vendor_code': 'L1'}}
        raw = [
            {'learning_objective_id': '1', 'top_score': 'M', 'learning_objectives': None},
            {'learning_objective_id': '1', 'top_score': 'MR', 'learning_objectives': None},
        ]
        out = _aggregate_lo_grades(raw, lo_lookup)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['m_count'], 2)
        self.assertEqual(out[0]['mr_count'], 1)
        self.assertTrue(out[0]['is_passed'])

    def test_multiple_los(self):
        students = self._build_data([
            {'learning_objective_id': '10', 'top_score': 'M', 'second_score': 'M'},
            {'learning_objective_id': '20', 'top_score': 'P', 'second_score': 'P'},
        ])
        los = [{'id': '10', 'name': 'LO-A'}, {'id': '20', 'name': 'LO-B'}]
        result = organize_by_learning_objectives(students, los)
        lo_a = next(lo for lo in result if lo['name'] == 'LO-A')
        lo_b = next(lo for lo in result if lo['name'] == 'LO-B')
        self.assertEqual(len(lo_a['students_with_2m']), 1)
        self.assertEqual(len(lo_b['students_with_0m']), 1)


class TestStudentSortKeyLastName(unittest.TestCase):
    """Students lists sort by family name (last token, or segment before comma)."""

    def test_last_token_used_for_first_last_format(self):
        rows = [
            {"full_name": "Bob Zenith"},
            {"full_name": "Zoe Adams"},
        ]
        ordered = sorted(rows, key=_student_row_sort_key)
        self.assertEqual([r["full_name"] for r in ordered], ["Zoe Adams", "Bob Zenith"])

    def test_comma_format_sorts_by_part_before_comma(self):
        self.assertEqual(
            _student_sort_key_last_name("Washington, George")[0],
            "washington",
        )

    def test_organize_buckets_sorted_by_last_name(self):
        students = [
            {
                "id": "1",
                "full_name": "Bob Zenith",
                "grades": [
                    {"learning_objective_id": "10", "top_score": "M", "second_score": "M"},
                ],
            },
            {
                "id": "2",
                "full_name": "Zoe Adams",
                "grades": [
                    {"learning_objective_id": "10", "top_score": "M", "second_score": "M"},
                ],
            },
        ]
        los = [{"id": "10", "name": "LO-A"}]
        result = organize_by_learning_objectives(students, los)
        names = [s["name"] for s in result[0]["students_with_2m"]]
        self.assertEqual(names, ["Adams Zoe", "Zenith Bob"])


class TestFormatNameLastFirst(unittest.TestCase):
    """Roster display: family name first."""

    def test_first_last_to_last_first(self):
        self.assertEqual(_format_name_last_first("John Smith"), "Smith John")

    def test_comma_form(self):
        self.assertEqual(_format_name_last_first("Washington, George"), "Washington George")

    def test_student_display_name_formats(self):
        self.assertEqual(
            _student_display_name({"id": "x", "full_name": "John Smith"}),
            "Smith John",
        )


class TestNormalizeProfile(unittest.TestCase):
    """Tests for normalize_profile — handles Supabase join shape quirks."""

    def test_dict_profile_returned_as_is(self):
        enrollment = {'profiles': {'id': '1', 'full_name': 'Alice'}}
        self.assertEqual(normalize_profile(enrollment), {'id': '1', 'full_name': 'Alice'})

    def test_list_profile_returns_first_element(self):
        enrollment = {'profiles': [{'id': '1', 'full_name': 'Alice'}]}
        self.assertEqual(normalize_profile(enrollment), {'id': '1', 'full_name': 'Alice'})

    def test_empty_list_returns_empty_dict(self):
        enrollment = {'profiles': []}
        self.assertEqual(normalize_profile(enrollment), {})

    def test_none_profile_returns_empty_dict(self):
        enrollment = {'profiles': None}
        self.assertEqual(normalize_profile(enrollment), {})

    def test_missing_key_returns_empty_dict(self):
        self.assertEqual(normalize_profile({}), {})


# ==========================================================================
# Constants Tests
# ==========================================================================

class TestConstants(unittest.TestCase):
    """Verify the shared grading constants are correct."""

    def test_mastery_grades_contains_expected_codes(self):
        self.assertIn('M', MASTERY_GRADES)
        self.assertIn('MR', MASTERY_GRADES)
        self.assertIn('R', MASTERY_GRADES)
        self.assertIn('RQ', MASTERY_GRADES)
        self.assertIn('P', MASTERY_GRADES)
        self.assertIn('X', MASTERY_GRADES)
        self.assertIn('A', MASTERY_GRADES)

    def test_default_required_ms(self):
        self.assertEqual(DEFAULT_REQUIRED_MS, 2)


# ==========================================================================
# Flask App Factory Tests
# ==========================================================================

class TestAppFactory(unittest.TestCase):
    """Tests for the create_app factory and basic configuration."""

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True

    def test_app_is_created(self):
        self.assertIsNotNone(self.app)

    def test_testing_flag(self):
        self.assertTrue(self.app.config['TESTING'])

    def test_blueprint_registered(self):
        self.assertIn('main', self.app.blueprints)

    def test_secret_key_is_set(self):
        self.assertIsNotNone(self.app.secret_key)

    def test_cors_configured(self):
        # CORS extension adds after_request handlers
        self.assertTrue(len(self.app.after_request_funcs) > 0)


# ==========================================================================
# Route Smoke Tests
# ==========================================================================

class TestRequestCacheMemoization(unittest.TestCase):
    """Repeated helper calls should hit DB once per request (memoized via flask.g).

    The assertions check call count, not just the result. The whole point of
    request-scope memoization is "exactly one DB round-trip per request per
    key", so a regression where the helper started returning the right value
    but bypassed the cache would still be a meaningful failure here.
    """

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True

    def _patch_supabase_admin(self, table_to_payload):
        """Return an unmagic-mock-style stub with .table().select().eq()....execute() chains."""
        from unittest.mock import MagicMock

        def make_query(payload):
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.in_.return_value = q
            q.is_.return_value = q
            q.ilike.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.single.return_value = q
            exec_mock = MagicMock()
            exec_mock.data = payload
            q.execute.return_value = exec_mock
            return q

        sa = MagicMock()
        call_count = {"total": 0}

        def table_side(name):
            call_count["total"] += 1
            return make_query(table_to_payload.get(name, []))

        sa.table.side_effect = table_side
        return sa, call_count

    def test_class_instructor_id_memoized(self):
        from app import routes as r
        sa, calls = self._patch_supabase_admin({
            "classes": [{"instructor_id": "u1"}],
        })
        with self.app.test_request_context("/"):
            with unittest.mock.patch.object(r, "supabase_admin", sa):
                a = r._class_instructor_id("c1")
                b = r._class_instructor_id("c1")
                self.assertEqual(a, "u1")
                self.assertEqual(b, "u1")
                self.assertEqual(calls["total"], 1)

    def test_assignment_belongs_to_class_memoized(self):
        from app import routes as r
        sa, calls = self._patch_supabase_admin({
            "assignments": [{"id": "a1"}],
        })
        with self.app.test_request_context("/"):
            with unittest.mock.patch.object(r, "supabase_admin", sa):
                self.assertTrue(r._assignment_belongs_to_class("c1", "a1"))
                self.assertTrue(r._assignment_belongs_to_class("c1", "a1"))
                self.assertEqual(calls["total"], 1)


class TestPublicRoutes(unittest.TestCase):
    """Verify public pages are reachable without authentication."""

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_login_page_returns_200(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)

    def test_root_returns_login(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_signup_page_returns_200(self):
        response = self.client.get('/signup')
        self.assertEqual(response.status_code, 200)

    def test_logout_redirects(self):
        response = self.client.get('/logout')
        self.assertEqual(response.status_code, 302)


class TestRequireJsonObject(unittest.TestCase):
    """Strict JSON parsing helper used by state-changing API routes.

    Both 400 and 415 are exercised because a lenient client (sending the wrong
    Content-Type) and a buggy client (sending malformed JSON) need to be
    distinguishable: 415 tells the caller to fix headers, 400 tells them to
    fix payload shape. Conflating them used to mask real client bugs.
    """

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True

    def test_invalid_json_returns_400(self):
        from app.routes import _require_json_object

        with self.app.test_request_context(
            "/dummy",
            method="POST",
            data="{not json",
            content_type="application/json",
        ):
            data, err = _require_json_object()
            self.assertIsNone(data)
            self.assertIsNotNone(err)
            self.assertEqual(err[1], 400)

    def test_non_json_content_type_returns_415(self):
        from app.routes import _require_json_object

        with self.app.test_request_context(
            "/dummy",
            method="POST",
            data='{"a": 1}',
            content_type="text/plain",
        ):
            data, err = _require_json_object()
            self.assertIsNone(data)
            self.assertEqual(err[1], 415)

    def test_array_json_returns_400(self):
        from app.routes import _require_json_object

        with self.app.test_request_context(
            "/dummy",
            method="POST",
            data="[]",
            content_type="application/json",
        ):
            data, err = _require_json_object()
            self.assertIsNone(data)
            self.assertEqual(err[1], 400)

    def test_valid_object_returns_data(self):
        from app.routes import _require_json_object

        with self.app.test_request_context(
            "/dummy",
            method="POST",
            data='{"rows": []}',
            content_type="application/json",
        ):
            data, err = _require_json_object()
            self.assertIsNone(err)
            self.assertEqual(data, {"rows": []})


class TestApiAuthGuards(unittest.TestCase):
    """Instructor JSON APIs reject unauthenticated callers before body parsing."""

    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_import_learning_objectives_unauthenticated_401(self):
        rv = self.client.post(
            "/api/class/test-class/import-learning-objectives",
            json={"rows": [{"vendor_code": "LO1", "description": "x", "required_ms": 2}]},
        )
        self.assertEqual(rv.status_code, 401)


class TestProtectedRoutes(unittest.TestCase):
    """Verify that protected pages redirect unauthenticated users."""

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_dashboard_requires_auth(self):
        response = self.client.get('/dashboard')
        # Should redirect to login when session has no user_id
        self.assertIn(response.status_code, (302, 404))

    def test_class_detail_requires_auth(self):
        response = self.client.get('/class/fake-id')
        self.assertIn(response.status_code, (302, 404))


class TestSaveGradesAutoConvertHwGuard(unittest.TestCase):
    """save_grades: stamps counts_for_mastery at entry time on first-entry M/MR.

    The pre-existing tests asserted the *old* M→I rewrite. The new contract
    keeps the letter as M / MR and instead persists a counts_for_mastery flag
    that is sticky for the lifetime of the row. See
    scripts/add_grades_counts_for_mastery.sql.
    """

    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _patched_save_grades_call(self, hw_map):
        """Common scaffolding: returns the rows the route attempted to upsert."""
        from app import routes as r
        captured_rows = []
        exec_m = MagicMock()
        q = MagicMock()
        q.upsert.side_effect = lambda rows, **kw: captured_rows.extend(rows) or exec_m
        # Existing-row pre-fetch in the new save_grades returns no rows by
        # default; MagicMock's __iter__ yields []. That's exactly the
        # "first-entry M" path we want to exercise here.
        sa = MagicMock()
        sa.table.return_value = q

        with self.client.session_transaction() as sess:
            sess["user_id"] = "inst1"
            sess["csrf_token"] = "test-csrf"

        headers = {"X-CSRF-Token": "test-csrf"}

        with unittest.mock.patch.object(r, "supabase_admin", sa), \
                unittest.mock.patch.object(r, "_instructor_owns_class", return_value=True), \
                unittest.mock.patch.object(r, "_assignment_belongs_to_class", return_value=True), \
                unittest.mock.patch.object(r, "_class_auto_convert_m_enabled", return_value=True), \
                unittest.mock.patch.object(r.Course, "get_lo_ids_for_class", return_value={"lo-x"}), \
                unittest.mock.patch.object(
                    r.Homework,
                    "get_hw_scores_map_for_assignment",
                    return_value=hw_map,
                ):
            rv = self.client.post(
                "/api/class/class-1/save-grades",
                json={"assignment_id": "asg-1", "grades": {"stu-1|lo-x": "M"}},
                headers=headers,
            )
        return rv, captured_rows

    def test_missing_hw_map_student_keeps_m_and_counts_for_mastery(self):
        rv, captured_rows = self._patched_save_grades_call(hw_map={})
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(len(captured_rows), 1)
        self.assertEqual(captured_rows[0]["top_score"], "M")
        # Missing HW => allow M (legacy behavior: don't penalize when we have
        # no data). The persisted flag is True so it still counts.
        self.assertTrue(captured_rows[0].get("counts_for_mastery", True))

    def test_hw_below_threshold_keeps_m_but_marks_non_counting(self):
        rv, captured_rows = self._patched_save_grades_call(hw_map={"stu-1": 40})
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(len(captured_rows), 1)
        # Letter stays M (the I letter is now a print-only visual on Reports).
        self.assertEqual(captured_rows[0]["top_score"], "M")
        # Non-counting flag captured at entry time; sticky going forward.
        self.assertEqual(captured_rows[0]["counts_for_mastery"], False)

    def test_hw_above_threshold_keeps_m_and_marks_counting(self):
        rv, captured_rows = self._patched_save_grades_call(hw_map={"stu-1": 80})
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(len(captured_rows), 1)
        self.assertEqual(captured_rows[0]["top_score"], "M")
        self.assertEqual(captured_rows[0]["counts_for_mastery"], True)


class TestHwPassPromotesNonCountingMasteries(unittest.TestCase):
    """save_hw_percentage with score=-1 flips non-counting M/MR to counting."""

    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_hw_pass_promotes_non_counting_masteries(self):
        from app import routes as r

        captured_upserts = []

        def make_query(table_name):
            q = MagicMock()
            if table_name == "homework_scores":
                q.upsert.return_value.execute.return_value = MagicMock()
            elif table_name == "grades":
                q.select.return_value.eq.return_value.in_.return_value.in_.return_value.execute.return_value = MagicMock(
                    data=[
                        {
                            "student_id": "stu-1",
                            "learning_objective_id": "lo-x",
                            "assignment_id": "asg-1",
                            "top_score": "M",
                            "counts_for_mastery": False,
                        }
                    ]
                )

                def upsert(rows, **kw):
                    captured_upserts.extend(rows)
                    return MagicMock(execute=MagicMock(return_value=MagicMock()))

                q.upsert.side_effect = upsert
            return q

        sa = MagicMock()
        sa.table.side_effect = make_query

        with self.client.session_transaction() as sess:
            sess["user_id"] = "inst1"
            sess["csrf_token"] = "test-csrf"

        headers = {"X-CSRF-Token": "test-csrf"}

        with unittest.mock.patch.object(r, "supabase_admin", sa), \
                unittest.mock.patch.object(r, "_instructor_owns_class", return_value=True), \
                unittest.mock.patch.object(
                    r.Homework,
                    "resolve_hw_group_storage_key",
                    return_value="hw-g1",
                ), \
                unittest.mock.patch.object(
                    r.Course,
                    "get_lo_ids_for_class",
                    return_value=["lo-x"],
                ):
            rv = self.client.post(
                "/api/class/class-1/save-hw-percentage",
                json={
                    "student_id": "stu-1",
                    "score": -1,
                    "assignment_id": "asg-1",
                },
                headers=headers,
            )

        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("promoted_masteries"), 1)
        self.assertEqual(len(captured_upserts), 1)
        self.assertTrue(captured_upserts[0].get("counts_for_mastery"))


# ==========================================================================
# Project mastery progress (assignment_type = 'project')
# ==========================================================================

class TestProjectMasteryProgress(unittest.TestCase):
    """Course.get_project_mastery_progress — pooled M/MR across linked LOs."""

    def _patch_supabase(self, assignment, ao_rows, grade_rows):
        """Return a context manager that mocks the three table queries."""
        def table_side_effect(name):
            m = MagicMock()
            if name == "assignments":
                m.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
                    MagicMock(data=[assignment] if assignment else [])
                )
            elif name == "assignment_objectives":
                m.select.return_value.eq.return_value.execute.return_value = (
                    MagicMock(data=ao_rows)
                )
            elif name == "grades":
                m.select.return_value.eq.return_value.in_.return_value.execute.return_value = (
                    MagicMock(data=grade_rows)
                )
            else:
                m.select.return_value.execute.return_value = MagicMock(data=[])
            return m

        sa = MagicMock()
        sa.table.side_effect = table_side_effect
        return unittest.mock.patch("app.models.supabase_admin", sa)

    def test_student_meets_project_threshold(self):
        assignment = {
            "id": "proj-1",
            "assignment_type": "project",
            "required_total_masteries": 5,
        }
        ao_rows = [
            {"learning_objective_id": "lo-a"},
            {"learning_objective_id": "lo-b"},
        ]
        grade_rows = [
            {"learning_objective_id": "lo-a", "top_score": "M", "counts_for_mastery": True},
            {"learning_objective_id": "lo-a", "top_score": "MR", "counts_for_mastery": True},
            {"learning_objective_id": "lo-b", "top_score": "M", "counts_for_mastery": True},
            {"learning_objective_id": "lo-b", "top_score": "M", "counts_for_mastery": True},
            {"learning_objective_id": "lo-b", "top_score": "M", "counts_for_mastery": True},
        ]
        with self._patch_supabase(assignment, ao_rows, grade_rows):
            result = Course.get_project_mastery_progress("stu-1", "proj-1")
        self.assertIsNotNone(result)
        self.assertEqual(result["mastery_count"], 5)
        self.assertEqual(result["required_total_masteries"], 5)
        self.assertTrue(result["is_complete"])
        self.assertEqual(result["progress_label"], "5 of 5")

    def test_student_below_project_threshold(self):
        assignment = {
            "id": "proj-1",
            "assignment_type": "project",
            "required_total_masteries": 5,
        }
        ao_rows = [{"learning_objective_id": "lo-a"}]
        grade_rows = [
            {"learning_objective_id": "lo-a", "top_score": "M", "counts_for_mastery": True},
            {"learning_objective_id": "lo-a", "top_score": "MR", "counts_for_mastery": True},
            {"learning_objective_id": "lo-a", "top_score": "R", "counts_for_mastery": True},
        ]
        with self._patch_supabase(assignment, ao_rows, grade_rows):
            result = Course.get_project_mastery_progress("stu-1", "proj-1")
        self.assertEqual(result["mastery_count"], 2)
        self.assertEqual(result["required_total_masteries"], 5)
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["progress_label"], "2 of 5")

    def test_grades_spread_across_multiple_linked_los(self):
        """Masteries on different linked LOs (and any assignment) all pool together."""
        assignment = {
            "id": "proj-1",
            "assignment_type": "project",
            "required_total_masteries": 4,
        }
        ao_rows = [
            {"learning_objective_id": "lo-a"},
            {"learning_objective_id": "lo-b"},
            {"learning_objective_id": "lo-c"},
        ]
        grade_rows = [
            {"learning_objective_id": "lo-a", "top_score": "M",
             "assignment_id": "asg-other", "counts_for_mastery": True},
            {"learning_objective_id": "lo-b", "top_score": "MR",
             "assignment_id": "asg-2", "counts_for_mastery": True},
            {"learning_objective_id": "lo-c", "top_score": "M",
             "assignment_id": "proj-1", "counts_for_mastery": True},
            {"learning_objective_id": "lo-c", "top_score": "M",
             "assignment_id": "asg-3", "counts_for_mastery": True},
        ]
        with self._patch_supabase(assignment, ao_rows, grade_rows):
            result = Course.get_project_mastery_progress("stu-1", "proj-1")
        self.assertEqual(result["mastery_count"], 4)
        self.assertTrue(result["is_complete"])
        self.assertEqual(result["progress_label"], "4 of 4")

    def test_non_counting_masteries_excluded(self):
        assignment = {
            "id": "proj-1",
            "assignment_type": "project",
            "required_total_masteries": 2,
        }
        ao_rows = [{"learning_objective_id": "lo-a"}]
        grade_rows = [
            {"learning_objective_id": "lo-a", "top_score": "M", "counts_for_mastery": True},
            {"learning_objective_id": "lo-a", "top_score": "M", "counts_for_mastery": False},
            {"learning_objective_id": "lo-a", "top_score": "MR", "counts_for_mastery": False},
        ]
        with self._patch_supabase(assignment, ao_rows, grade_rows):
            result = Course.get_project_mastery_progress("stu-1", "proj-1")
        self.assertEqual(result["mastery_count"], 1)
        self.assertFalse(result["is_complete"])

    def test_mastery_opp_assignment_returns_none(self):
        assignment = {
            "id": "asg-1",
            "assignment_type": "mastery_opp",
            "required_total_masteries": 5,
        }
        with self._patch_supabase(assignment, [{"learning_objective_id": "lo-a"}], [
            {"learning_objective_id": "lo-a", "top_score": "M", "counts_for_mastery": True},
        ]):
            self.assertIsNone(Course.get_project_mastery_progress("stu-1", "asg-1"))

    def test_exam_assignment_returns_none(self):
        assignment = {
            "id": "exam-1",
            "assignment_type": "exam",
            "required_total_masteries": None,
        }
        with self._patch_supabase(assignment, [], []):
            self.assertIsNone(Course.get_project_mastery_progress("stu-1", "exam-1"))

    def test_count_counted_masteries_helper_matches_aggregate_rule(self):
        """Pure helper: None/missing counts_for_mastery defaults to counting."""
        grades = [
            {"top_score": "M"},  # missing flag → counts
            {"top_score": "MR", "counts_for_mastery": None},
            {"top_score": "M", "counts_for_mastery": False},
            {"top_score": "R", "counts_for_mastery": True},
            {"top_score": "I", "counts_for_mastery": True},
        ]
        self.assertEqual(Course._count_counted_masteries(grades), 2)

    def test_aggregate_lo_grades_still_per_lo_for_existing_types(self):
        """Sanity: per-LO required_ms path is unchanged by project feature."""
        lo_lookup = {
            "1": {"id": "1", "name": "LO", "vendor_code": "L1", "required_ms": 2},
        }
        raw = [
            {"learning_objective_id": "1", "top_score": "M", "counts_for_mastery": True},
            {"learning_objective_id": "1", "top_score": "MR", "counts_for_mastery": True},
        ]
        out = _aggregate_lo_grades(raw, lo_lookup)
        self.assertEqual(out[0]["m_count"], 2)
        self.assertTrue(out[0]["is_passed"])


if __name__ == '__main__':
    unittest.main()
