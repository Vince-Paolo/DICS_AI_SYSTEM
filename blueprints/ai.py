from flask import Blueprint, flash, redirect, session, url_for

from services import permissions as permission_service

ai_bp = Blueprint('ai', __name__)


@ai_bp.route('/ai-prediction', methods=['GET', 'POST'])
def ai_prediction():
    if 'username' not in session:
        return redirect(url_for('login'))
    if permission_service.is_dispatcher() or permission_service.is_imt():
        flash(
            'Manual AI predictions have been replaced by per-emergency response decision support.',
            'info',
        )
        return redirect(url_for('dispatcher.decision_support'))
    flash('You do not have permission to access response decision support.', 'danger')
    return redirect(url_for('dashboard'))
