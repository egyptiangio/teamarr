// Teamarr - Sports Team EPG Generator JavaScript

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Teamarr initialized');

    // Load saved theme preference
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.body.classList.add(savedTheme + '-theme');
    updateThemeIcon(savedTheme);

    // Convert Flask flash messages to notifications
    convertFlashMessages();
});

// Theme toggle functionality
function toggleTheme() {
    const body = document.body;
    const isDark = body.classList.contains('dark-theme');

    body.classList.remove('dark-theme', 'light-theme');
    const newTheme = isDark ? 'light' : 'dark';
    body.classList.add(newTheme + '-theme');

    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.textContent = theme === 'dark' ? '🌙' : '☀️';
    }
}

// Utility: Insert text at cursor position in textarea
function insertAtCursor(textarea, text) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const value = textarea.value;

    textarea.value = value.substring(0, start) + text + value.substring(end);

    // Move cursor after inserted text
    const newPos = start + text.length;
    textarea.setSelectionRange(newPos, newPos);
    textarea.focus();
}

// Confirm delete actions
function confirmDelete(message) {
    return confirm(message || 'Are you sure you want to delete this?');
}

// Notification System
function showNotification(message, type = 'info', duration = 10000, title = null) {
    const container = document.getElementById('notification-container');
    if (!container) return;

    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;

    const icons = {
        success: '✅',
        error: '❌',
        info: '📡',
        warning: '⚠️'
    };

    const titles = {
        success: title || 'Success',
        error: title || 'Error',
        info: title || 'Info',
        warning: title || 'Warning'
    };

    notification.innerHTML = `
        <div class="notification-icon">${icons[type]}</div>
        <div class="notification-content">
            <div class="notification-title">${titles[type]}</div>
            <div class="notification-message">${message}</div>
        </div>
        <button class="notification-close" onclick="closeNotification(this)">×</button>
    `;

    container.appendChild(notification);

    // Auto-dismiss after duration
    if (duration > 0) {
        setTimeout(() => {
            closeNotification(notification.querySelector('.notification-close'));
        }, duration);
    }

    return notification;
}

function closeNotification(button) {
    const notification = button.parentElement || button;
    notification.classList.add('hiding');
    setTimeout(() => {
        notification.remove();
    }, 300); // Match animation duration
}

// Convert Flask flash messages to popup notifications
function convertFlashMessages() {
    const flashMessages = document.querySelector('.flash-messages');
    if (!flashMessages) return;

    const alerts = flashMessages.querySelectorAll('.alert');
    alerts.forEach(alert => {
        const message = alert.textContent.replace('×', '').trim();
        let type = 'info';

        if (alert.classList.contains('alert-success')) type = 'success';
        else if (alert.classList.contains('alert-error')) type = 'error';
        else if (alert.classList.contains('alert-warning')) type = 'warning';

        showNotification(message, type);
    });

    // Hide the flash messages container
    flashMessages.style.display = 'none';
}

// EPG Generation with Progress Tracking
function generateEPGWithProgress(buttonId = 'generate-epg-btn', returnTo = null) {
    const btn = document.getElementById(buttonId);
    if (!btn) {
        console.error(`Button with ID '${buttonId}' not found`);
        return;
    }

    // Disable button during generation
    const originalText = btn.textContent || btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '⏳ Generating...';

    // Create persistent notification for progress
    let progressNotification = showNotification('Initializing EPG generation...', 'info', 0, 'Generating EPG');

    // Listen to Server-Sent Events stream
    const eventSource = new EventSource('/generate/stream');

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);

        if (data.status === 'starting') {
            updateProgressNotification(progressNotification, data.message, 0, null);
        }
        else if (data.status === 'progress') {
            const progressText = `Processing ${data.team_name} (${data.current}/${data.total})`;
            updateProgressNotification(progressNotification, progressText, data.percent, data.team_name);
        }
        else if (data.status === 'finalizing') {
            updateProgressNotification(progressNotification, data.message, 95, null);
        }
        else if (data.status === 'complete') {
            // Close SSE connection
            eventSource.close();

            // Close progress notification
            closeNotification(progressNotification.querySelector('.notification-close'));

            // Show success notification
            showNotification(data.message, 'success', 10000);

            // Re-enable button
            btn.disabled = false;
            btn.textContent = originalText;

            // Reload page to show updated stats
            setTimeout(() => window.location.reload(), 2000);
        }
        else if (data.status === 'error') {
            // Close SSE connection
            eventSource.close();

            // Close progress notification
            closeNotification(progressNotification.querySelector('.notification-close'));

            // Show error notification
            showNotification('Error: ' + data.message, 'error', 10000);

            // Re-enable button
            btn.disabled = false;
            btn.textContent = originalText;
        }
    };

    eventSource.onerror = function(error) {
        console.error('SSE Error:', error);
        eventSource.close();

        // Close progress notification
        closeNotification(progressNotification.querySelector('.notification-close'));

        // Show error
        showNotification('Connection error during EPG generation', 'error', 10000);

        // Re-enable button
        btn.disabled = false;
        btn.textContent = originalText;
    };
}

function updateProgressNotification(notification, message, percent, teamName) {
    const messageEl = notification.querySelector('.notification-message');
    if (messageEl) {
        let html = message;
        if (percent !== null) {
            html += `<br><div style="margin-top: 0.5rem; background: var(--bg-tertiary); border-radius: 4px; overflow: hidden; height: 8px;">
                <div style="background: var(--primary); height: 100%; width: ${percent}%; transition: width 0.3s;"></div>
            </div>
            <div style="margin-top: 0.25rem; font-size: 0.875rem; color: var(--text-muted);">${percent}%</div>`;
        }
        messageEl.innerHTML = html;
    }
}
