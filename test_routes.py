# test_routes.py
"""
Test Suite for Project Clarity Application

This file contains unit tests for the Flask routes defined in routes.py.
Run these tests using: python -m pytest test_routes.py -v

Install required packages:
pip install pytest flask
"""

import pytest
from flask import Flask
from app.routes import main_bp, classes, organize_by_learning_objectives


# ============================================================================
# PYTEST FIXTURES
# ============================================================================

@pytest.fixture
def app():
    """Create and configure a test Flask application."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    app.register_blueprint(main_bp)
    return app


@pytest.fixture
def client(app):
    """Create a test client for the Flask application."""
    return app.test_client()


@pytest.fixture
def sample_students():
    """Sample student data for testing."""
    return [
        {
            'id': '1',
            'name': 'Adams, John',
            'learning_objectives': [
                {'name': 'LO1', 'top_score': 'M', 'second_score': 'M'},
                {'name': 'LO2', 'top_score': 'M', 'second_score': 'R'},
            ]
        },
        {
            'id': '2',
            'name': 'Smith, Jane',
            'learning_objectives': [
                {'name': 'LO1', 'top_score': 'R', 'second_score': 'X'},
                {'name': 'LO2', 'top_score': 'M', 'second_score': 'M'},
            ]
        }
    ]


# ============================================================================
# TEST CASES
# ============================================================================

class TestRoutes:
    """Test cases for main application routes."""
    
    
    def test_class_detail_page_invalid_id(self, client):
        """
        Test Case 1: Class detail page redirects for invalid class ID
        
        Verifies that accessing a non-existent class redirects back
        to the instructor main page.
        """
        response = client.get('/class/999', follow_redirects=False)
        assert response.status_code == 302  # Redirect status
        assert '/instructor' in response.location or '/' in response.location
    
    
    def test_select_class_form_submission(self, client):
        """
        Test Case 2: Selecting an existing class redirects correctly
        
        Verifies that submitting the class selection form with a valid
        class ID redirects to the correct class detail page.
        """
        response = client.post('/select_class', 
                              data={'class_id': '1'},
                              follow_redirects=False)
        assert response.status_code == 302
        assert '/class/1' in response.location
    
    
    def test_select_class_invalid_id(self, client):
        """
        Test Case 3: Selecting invalid class redirects to main page
        
        Verifies that submitting an invalid class ID redirects back
        to the instructor main page.
        """
        response = client.post('/select_class',
                              data={'class_id': '999'},
                              follow_redirects=False)
        assert response.status_code == 302
        assert '/instructor' in response.location or '/' in response.location
    
    
    def test_add_new_class(self, client):
        """
        Test Case 4: Adding a new class creates it successfully
        
        Verifies that submitting the add class form creates a new class
        and redirects to its detail page.
        """
        initial_class_count = len(classes)
        
        response = client.post('/add_class',
                              data={
                                  'name': 'Test Section',
                                  'number': 'TEST 101',
                                  'semester': 'Spring 2026',
                                  'start': '01/15/2026',
                                  'end': '05/15/2026',
                                  'days': 'TR'
                              },
                              follow_redirects=False)
        
        assert response.status_code == 302
        assert len(classes) == initial_class_count + 1
        # Should redirect to the new class detail page
        assert '/class/' in response.location
    
    
    def test_search_api_students_view(self, client):
        """
        Test Case 5: Search API filters students correctly
        
        Verifies that the search API endpoint filters students
        by name in the students view.
        """
        response = client.get('/api/search?query=john&view=students&class_id=1')
        assert response.status_code == 200
        json_data = response.get_json()
        assert 'students' in json_data
        # Should find "Adams, John"
        assert len(json_data['students']) >= 1


class TestHelperFunctions:
    """Test cases for helper functions."""
    
    def test_organize_by_learning_objectives(self, sample_students):
        """
        Test Case 6: Helper function organizes data correctly
        
        Verifies that the organize_by_learning_objectives function
        correctly groups students by mastery level for each LO.
        """
        result = organize_by_learning_objectives(sample_students)
        
        # Should have 2 learning objectives (LO1 and LO2)
        assert len(result) == 2
        
        # Find LO1
        lo1 = next(lo for lo in result if lo['name'] == 'LO1')
        
        # Check mastery groupings for LO1
        assert lo1['two_m_count'] == 1  # Adams has 2 M's
        assert lo1['one_m_count'] == 0
        assert lo1['zero_m_count'] == 1  # Smith has 0 M's
        
        # Verify student names in correct groups
        assert any(s['name'] == 'Adams, John' for s in lo1['students_with_2m'])
        assert any(s['name'] == 'Smith, Jane' for s in lo1['students_with_0m'])


class TestDataIntegrity:
    """Test cases for data structure integrity."""
    
    def test_sample_data_structure(self):
        """
        Test Case 7: Sample data has correct structure
        
        Verifies that the classes dictionary has the expected
        structure and required fields.
        """
        assert '1' in classes
        assert '2' in classes
        
        class_1 = classes['1']
        assert 'id' in class_1
        assert 'name' in class_1
        assert 'semester' in class_1
        assert 'students' in class_1
        assert isinstance(class_1['students'], list)
        
        if len(class_1['students']) > 0:
            student = class_1['students'][0]
            assert 'id' in student
            assert 'name' in student
            assert 'learning_objectives' in student


class TestEdgeCases:
    """Test cases for edge cases and error handling."""
    
    def test_empty_search_query(self, client):
        """
        Test Case 8: Search with empty query returns all results
        
        Verifies that searching with an empty query returns all
        students or learning objectives.
        """
        response = client.get('/api/search?query=&view=students&class_id=1')
        assert response.status_code == 200
        json_data = response.get_json()
        # Empty search should return all students
        assert len(json_data['students']) == len(classes['1']['students'])
    
    
    def test_search_nonexistent_class(self, client):
        """
        Test Case 9: Search for non-existent class returns error
        
        Verifies that the search API returns a 404 error when
        searching in a non-existent class.
        """
        response = client.get('/api/search?query=test&view=students&class_id=999')
        assert response.status_code == 404
        json_data = response.get_json()
        assert 'error' in json_data


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == '__main__':
    """
    Run tests directly with: python test_routes.py
    
    For more detailed output, use: python -m pytest test_routes.py -v
    For coverage report, use: python -m pytest test_routes.py --cov=app
    """
    pytest.main([__file__, '-v', '--tb=short'])