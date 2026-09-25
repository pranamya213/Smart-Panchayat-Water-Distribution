document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const wardCards = document.querySelectorAll('.ward-card');
    const selectedWardDisplay = document.getElementById('selected-ward-display');
    const durationInput = document.getElementById('duration-input');
    const btnCreate = document.getElementById('btn-create');
    const btnStopGlobal = document.getElementById('btn-stop-global');
    
    const errorMessage = document.getElementById('error-message');
    const successMessage = document.getElementById('success-message');
    
    const pumpStatusEl = document.getElementById('pump-status');
    const currentWardEl = document.getElementById('current-ward');
    const remainingTimeEl = document.getElementById('remaining-time');
    
    const schedulesTbody = document.getElementById('schedules-tbody');
    const historyTbody = document.getElementById('history-tbody');
    const activityList = document.getElementById('activity-list');
    
    // State
    let selectedWard = null;
    let runningScheduleId = null;
    let remainingSecondsLocal = 0;
    let localTimerInterval = null;
    let lastKnownSchedules = [];

    // Initialize polling
    fetchStatus();
    setInterval(fetchStatus, 3000); // Poll every 3s

    // Ward Selection Logic
    wardCards.forEach(card => {
        card.addEventListener('click', () => {
            wardCards.forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            selectedWard = card.dataset.ward;
            selectedWardDisplay.textContent = `Selected Ward: ${selectedWard}`;
            hideMessages();
        });
    });

    // Create Schedule
    btnCreate.addEventListener('click', () => {
        hideMessages();
        
        if (!selectedWard) {
            showError("Please select a ward first.");
            return;
        }
        
        const durationValue = parseInt(durationInput.value);
        if (isNaN(durationValue) || durationValue < 1 || durationValue > 60) {
            showError("Please enter a duration between 1 and 60 minutes.");
            return;
        }
        
        btnCreate.disabled = true;
        fetch('/api/schedule', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ward: selectedWard, duration_minutes: durationValue })
        })
        .then(res => res.json())
        .then(data => {
            btnCreate.disabled = false;
            if (data.error) {
                showError(data.error);
            } else {
                showSuccess("Schedule created successfully.");
                durationInput.value = '';
                wardCards.forEach(c => c.classList.remove('selected'));
                selectedWard = null;
                selectedWardDisplay.textContent = 'No ward selected';
                fetchStatus();
            }
        })
        .catch(err => {
            btnCreate.disabled = false;
            showError("Failed to create schedule.");
            console.error(err);
        });
    });

    // Stop Global
    btnStopGlobal.addEventListener('click', () => {
        if (!runningScheduleId) return;
        btnStopGlobal.disabled = true;
        
        fetch(`/api/schedule/${runningScheduleId}/stop`, { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            btnStopGlobal.disabled = false;
            if (data.error) {
                showError(data.error);
            } else {
                logActivity(`Supply stopped manually`);
                fetchStatus();
            }
        })
        .catch(err => {
            btnStopGlobal.disabled = false;
            showError("Failed to stop supply.");
        });
    });

    // Fetch Status
    function fetchStatus() {
        fetch('/api/status')
        .then(res => res.json())
        .then(data => {
            updateDashboard(data);
        })
        .catch(err => console.error("Error fetching status", err));
    }

    function updateDashboard(data) {
        const running = data.running;
        const allSchedules = data.schedules;
        
        // Handle Running State
        if (running) {
            if (runningScheduleId !== running.id) {
                // Newly started or page refreshed
                runningScheduleId = running.id;
                remainingSecondsLocal = running.current_remaining;
                
                pumpStatusEl.textContent = "RUNNING";
                pumpStatusEl.className = "status-value running";
                currentWardEl.textContent = running.ward;
                
                btnStopGlobal.classList.remove('hidden');
                
                startLocalCountdown();
                
                // Only log if it's new to us, but for a simple approach we just trust DB
                // In a perfect system we check if we already logged it.
            } else {
                // Sync remaining time with server to avoid drift
                remainingSecondsLocal = running.current_remaining;
            }
        } else {
            if (runningScheduleId !== null) {
                // It just stopped or completed
                runningScheduleId = null;
                clearInterval(localTimerInterval);
                localTimerInterval = null;
                
                pumpStatusEl.textContent = "STOPPED";
                pumpStatusEl.className = "status-value stopped";
                // Keep the current ward visible but update time to 00:00
                remainingSecondsLocal = 0;
                updateTimeDisplay();
                
                btnStopGlobal.classList.add('hidden');
            }
        }

        // Render Tables
        renderTables(allSchedules);
    }

    function startLocalCountdown() {
        if (localTimerInterval) clearInterval(localTimerInterval);
        
        updateTimeDisplay();
        localTimerInterval = setInterval(() => {
            remainingSecondsLocal--;
            if (remainingSecondsLocal <= 0) {
                remainingSecondsLocal = 0;
                clearInterval(localTimerInterval);
                localTimerInterval = null;
                // Fetch status to let server mark it completed
                fetchStatus();
            }
            updateTimeDisplay();
        }, 1000);
    }

    function updateTimeDisplay() {
        if (remainingSecondsLocal < 0) remainingSecondsLocal = 0;
        const mins = Math.floor(remainingSecondsLocal / 60);
        const secs = remainingSecondsLocal % 60;
        remainingTimeEl.textContent = 
            `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }

    function renderTables(schedules) {
        schedulesTbody.innerHTML = '';
        historyTbody.innerHTML = '';
        
        const activeStatuses = ['SCHEDULED', 'RUNNING'];
        
        schedules.forEach(sch => {
            if (activeStatuses.includes(sch.status)) {
                // Scheduled / Running list
                const tr = document.createElement('tr');
                let actionHTML = '';
                if (sch.status === 'SCHEDULED') {
                    actionHTML = `<button class="btn btn-primary btn-small" onclick="window.startSchedule(${sch.id})">Start</button>`;
                } else if (sch.status === 'RUNNING') {
                    actionHTML = `<em>Running</em>`;
                }
                
                tr.innerHTML = `
                    <td>${sch.ward}</td>
                    <td>${sch.duration_minutes} min</td>
                    <td><span class="status-badge status-${sch.status}">${sch.status}</span></td>
                    <td>${actionHTML}</td>
                `;
                schedulesTbody.appendChild(tr);
            } else {
                // History list
                const tr = document.createElement('tr');
                const finishedTime = sch.completed_at || sch.stopped_at || '-';
                tr.innerHTML = `
                    <td>${sch.ward}</td>
                    <td>${sch.duration_minutes} min</td>
                    <td><span class="status-badge status-${sch.status}">${sch.status}</span></td>
                    <td>${sch.started_at ? sch.started_at.split(' ')[1] : '-'}</td>
                    <td>${finishedTime !== '-' ? finishedTime.split(' ')[1] : '-'}</td>
                `;
                historyTbody.appendChild(tr);
            }
        });
        
        if (schedulesTbody.children.length === 0) {
            schedulesTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:#666;">No active schedules</td></tr>`;
        }
        if (historyTbody.children.length === 0) {
            historyTbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#666;">No history records</td></tr>`;
        }
    }

    // Expose start function to global scope for inline onclick handler
    window.startSchedule = function(id) {
        if (runningScheduleId) {
            showError("Another ward is currently receiving water.");
            return;
        }
        
        fetch(`/api/schedule/${id}/start`, { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                showError(data.error);
            } else {
                logActivity(`Supply started`);
                fetchStatus();
            }
        })
        .catch(err => showError("Failed to start supply."));
    };

    function showError(msg) {
        successMessage.classList.add('hidden');
        errorMessage.textContent = msg;
        errorMessage.classList.remove('hidden');
        setTimeout(() => errorMessage.classList.add('hidden'), 4000);
    }

    function showSuccess(msg) {
        errorMessage.classList.add('hidden');
        successMessage.textContent = msg;
        successMessage.classList.remove('hidden');
        setTimeout(() => successMessage.classList.add('hidden'), 4000);
    }
    
    function hideMessages() {
        errorMessage.classList.add('hidden');
        successMessage.classList.add('hidden');
    }

    function logActivity(message) {
        const emptyMsg = activityList.querySelector('.empty-activity');
        if (emptyMsg) emptyMsg.remove();
        
        const now = new Date();
        const timeString = now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        
        const li = document.createElement('li');
        li.innerHTML = `<span class="activity-time">${timeString}</span> <span class="activity-msg">— ${message}</span>`;
        
        activityList.insertBefore(li, activityList.firstChild);
        if (activityList.children.length > 8) {
            activityList.removeChild(activityList.lastChild);
        }
    }
});
