# app/core/auth.py
from functools import wraps
from flask import redirect, url_for, flash, request
from flask_login import current_user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("You do not have permission to view this page.", "danger")
            return redirect(url_for("index.index"))
        return f(*args, **kwargs)
    return decorated_function
