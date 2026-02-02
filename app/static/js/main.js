// Global utility functions
console.log('Project Clarity loaded');

// will add any shared JavaScript functionality here
function submitSignup(event) {
    event.preventDefault();

    const name = document.getElementById('name').value;
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    const role = document.getElementById('role').value;

    if (password !== confirmPassword) {
        alert('Passwords do not match!');
        return;
    }

    const data = { name, email, password, role };

    fetch('/api/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(r => r.json())
    .then(response => {
        if (response.success) {
            window.location.href = `/${role}/dashboard`;
        } else {
            alert(response.message || 'Sign-up failed. Try again.');
        }
    })
    .catch(() => alert('Error during sign-up.'));
}
