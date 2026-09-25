document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const wardCards = document.querySelectorAll('.ward-card');
    const selectedWardDisplay = document.getElementById('selected-ward-display');
    const durationInput = document.getElementById('duration-input');
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const errorMessage = document.getElementById('error-message');
    
    // Status Displays
    const pumpStatusEl = document.getElementById('pump-status');
    const currentWardEl = document.getElementById('current-ward');
    const remainingTimeEl = document.getElementById('remaining-time');
    const activityList = document.getElementById('activity-list');
    
    // State Variables
    let selectedWard = null;
    let isRunning = false;
    let timerInterval = null;
    let remainingSeconds = 0;

    // Initialize Activity List
    logActivity("System Initialized");

    // Ward Selection Logic
    wardCards.forEach(card => {
        card.addEventListener('click', () => {
            if (isRunning) return; // Prevent changing ward while running
            
            // Remove selected class from all
            wardCards.forEach(c => c.classList.remove('selected'));
            
            // Add selected class to clicked
            card.classList.add('selected');
            
            // Update state and display
            selectedWard = card.dataset.ward;
            selectedWardDisplay.textContent = `Selected Ward: ${selectedWard}`;
            
            // Clear any previous error
            hideError();
        });
    });

    // Start Button Logic
    btnStart.addEventListener('click', () => {
        if (isRunning) return;
        
        hideError();
        
        // Validation
        if (!selectedWard) {
            showError("Please select a ward first.");
            return;
        }
        
        const durationValue = parseInt(durationInput.value);
        if (isNaN(durationValue) || durationValue < 1 || durationValue > 60) {
            showError("Please enter a duration between 1 and 60 minutes.");
            return;
        }
        
        // Start Supply
        startSupply(selectedWard, durationValue);
    });

    // Stop Button Logic
    btnStop.addEventListener('click', () => {
        if (!isRunning) return;
        
        stopSupply(true);
    });

    // Function to Start Supply
    function startSupply(ward, durationMins) {
        isRunning = true;
        remainingSeconds = durationMins * 60;
        
        // Update UI state
        btnStart.disabled = true;
        btnStop.disabled = false;
        durationInput.disabled = true;
        wardCards.forEach(c => c.style.pointerEvents = 'none');
        
        // Update Status Cards
        pumpStatusEl.textContent = "RUNNING";
        pumpStatusEl.className = "status-value running";
        currentWardEl.textContent = ward;
        updateTimeDisplay();
        
        logActivity(`${ward} supply started`);
        
        // Start Countdown Timer
        timerInterval = setInterval(() => {
            remainingSeconds--;
            updateTimeDisplay();
            
            if (remainingSeconds <= 0) {
                stopSupply(false); // Auto completion
            }
        }, 1000);
    }

    // Function to Stop Supply
    function stopSupply(isManual) {
        isRunning = false;
        clearInterval(timerInterval);
        
        // Update UI state
        btnStart.disabled = false;
        btnStop.disabled = true;
        durationInput.disabled = false;
        wardCards.forEach(c => c.style.pointerEvents = 'auto');
        
        // Update Status Cards
        pumpStatusEl.textContent = "STOPPED";
        pumpStatusEl.className = "status-value stopped";
        remainingSeconds = 0;
        updateTimeDisplay();
        
        if (isManual) {
            logActivity(`${selectedWard} supply stopped`);
        } else {
            logActivity(`${selectedWard} supply completed`);
        }
    }

    // Function to Update Timer Display
    function updateTimeDisplay() {
        const mins = Math.floor(remainingSeconds / 60);
        const secs = remainingSeconds % 60;
        remainingTimeEl.textContent = 
            `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }

    // Function to Show Error
    function showError(msg) {
        errorMessage.textContent = msg;
        errorMessage.classList.remove('hidden');
        // Auto hide after 3 seconds
        setTimeout(hideError, 3000);
    }
    
    // Function to Hide Error
    function hideError() {
        errorMessage.classList.add('hidden');
    }

    // Function to Log Activity
    function logActivity(message) {
        // Remove empty state message if exists
        const emptyMsg = activityList.querySelector('.empty-activity');
        if (emptyMsg) {
            emptyMsg.remove();
        }
        
        const now = new Date();
        // Get localized time in standard AM/PM format matching design (e.g. 10:30 AM)
        const timeString = now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        
        const li = document.createElement('li');
        li.innerHTML = `<span class="activity-time">${timeString}</span> <span class="activity-msg">— ${message}</span>`;
        
        // Add to top of list
        activityList.insertBefore(li, activityList.firstChild);
        
        // Keep list reasonable size (max 10)
        if (activityList.children.length > 10) {
            activityList.removeChild(activityList.lastChild);
        }
    }
});
