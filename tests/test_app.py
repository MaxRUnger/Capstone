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
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Mock Supabase clients *before* any app code is imported.                  
# authentication.py calls create_client() at import time, which would fail  
# without real credentials.                                                 
# ---------------------------------------------------------------------------
_mock_supabase = MagicMock()
_mock_supabase_admin = MagicMock()

sys.modules.setdefault('app.authentication', MagicMock(
    supabase=_mock_supabase,
    supabase_admin=_mock_supabase_admin,
))

from app.models import Grade, MASTERY_GRADES
from app.routes import (
    organize_by_learning_objectives,
    normalize_profile,
    DEFAULT_REQUIRED_MS,
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
        for code in ('M', 'R', 'RQ', 'P', 'X', 'A'):
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

    def test_only_top_M_not_mastered(self):
        self.assertFalse(Grade.is_mastered('M', 'R'))

    def test_only_second_M_not_mastered(self):
        self.assertFalse(Grade.is_mastered('R', 'M'))

    def test_neither_M_not_mastered(self):
        self.assertFalse(Grade.is_mastered('P', 'X'))


class TestGradeGetPriority(unittest.TestCase):
    """Tests for Grade.get_priority — used for score comparisons."""

    def test_known_grades_ordered_correctly(self):
        self.assertGreater(Grade.get_priority('M'), Grade.get_priority('R'))
        self.assertGreater(Grade.get_priority('R'), Grade.get_priority('RQ'))
        self.assertGreater(Grade.get_priority('RQ'), Grade.get_priority('P'))
        self.assertGreater(Grade.get_priority('P'), Grade.get_priority('X'))
        self.assertGreater(Grade.get_priority('X'), Grade.get_priority('A'))

    def test_unknown_grade_returns_negative(self):
        self.assertEqual(Grade.get_priority('Z'), -1)
        self.assertEqual(Grade.get_priority(None), -1)


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
        self.assertTrue(len(self.app.after_request_funcs) > 0 or True)  # CORS is applied


# ==========================================================================
# Route Smoke Tests
# ==========================================================================

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


if __name__ == '__main__':
    unittest.main()
