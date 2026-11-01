// Send to the login page when the login has expired (the AI password check also answers 401)
const originalFetch = window.fetch;
window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (response.status === 401 && !String(args[0]).startsWith('/api/auth')) {
        window.location.href = '/login';
    }
    // 503 means the Gemini API key is not set yet
    if (response.status === 503) {
        response.clone().json().then((data) => showNotification(data.detail, 'error')).catch(() => {});
    }
    return response;
};

// Global state
let currentSessionId = null;
let isAiEnabled = false;
let currentConfig = {};
let chatStarted = false;
let currentAgentName = null;

// DOM elements
const domainSelect = document.getElementById('domainSelect');
const jobPositionSelect = document.getElementById('jobPositionSelect');
const languageSelect = document.getElementById('languageSelect');
const difficultySelect = document.getElementById('difficultySelect');
const reformSelect = document.getElementById('reformSelect');
const detailBtn = document.getElementById('detailBtn');
const startBtn = document.getElementById('startBtn');
const restartBtn = document.getElementById('restartBtn');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const chatMessages = document.getElementById('chatMessages');
const configBtn = document.getElementById('configBtn');
const aiStatus = document.getElementById('aiStatus');
const sentimentSlider = document.getElementById('sentimentSlider');
const sentimentValue = document.getElementById('sentimentValue');
const closeCaseBtn = document.getElementById('closeCaseBtn');
const emotionTitle = document.getElementById('emotionTitle');
const emotionText = document.getElementById('emotionText');
const suggestionsPanel = document.getElementById('suggestionsPanel');
const suggestionsList = document.getElementById('suggestionsList');
const configCollapseBtn = document.getElementById('configCollapseBtn');
const configContent = document.getElementById('configContent');
const sidebar = document.querySelector('.sidebar');
const configHeader = document.querySelector('.config-header');
const teamCollapseBtn = document.getElementById('teamCollapseBtn');
const teamContent = document.getElementById('teamContent');
const teamSidebar = document.querySelector('.team-sidebar');
const teamHeader = document.querySelector('.team-header');

// Modal elements
const aiConfigModal = document.getElementById('aiConfigModal');
const complaintModal = document.getElementById('complaintModal');
const evaluationModal = document.getElementById('evaluationModal');
const loadingModal = document.getElementById('loadingModal');
const agentNameModal = document.getElementById('agentNameModal');
const loadingTitle = document.getElementById('loadingTitle');
const loadingMessage = document.getElementById('loadingMessage');
const passwordInput = document.getElementById('passwordInput');
const agentNameInput = document.getElementById('agentNameInput');

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
    loadCurrentUser();
    initializeApp();
    setupEventListeners();
    initializeConfigCollapse();
    initializeTeamCollapse();
    
    // Initialize AI features as disabled
    toggleAiFeatures(false);
});

async function loadCurrentUser() {
    const response = await fetch('/api/me');
    if (!response.ok) return;
    const user = await response.json();
    document.getElementById('currentUser').textContent = user.username;
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login';
}

async function initializeApp() {
    try {
        // Load domains
        const response = await fetch('/api/domains');
        const data = await response.json();
        
        domainSelect.innerHTML = '<option value="">Select Domain...</option>';
        data.domains.forEach(domain => {
            const option = document.createElement('option');
            option.value = domain;
            option.textContent = domain;
            domainSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading domains:', error);
        showNotification('Error loading domains', 'error');
    }
}

function setupEventListeners() {
    // Domain selection
    domainSelect.addEventListener('change', handleDomainChange);
    
    // Job position selection
    jobPositionSelect.addEventListener('change', handleJobPositionChange);
    
    // Configuration buttons
    detailBtn.addEventListener('click', showComplaintDetail);
    startBtn.addEventListener('click', showAgentNameModal);
    restartBtn.addEventListener('click', restartChat);
    
    // Chat functionality
    chatInput.addEventListener('keypress', handleChatInputKeypress);
    sendBtn.addEventListener('click', sendMessage);
    
    // AI configuration
    configBtn.addEventListener('click', () => aiConfigModal.style.display = 'block');
    document.getElementById('closeAiModal').addEventListener('click', () => aiConfigModal.style.display = 'none');
    document.getElementById('cancelAiBtn').addEventListener('click', () => aiConfigModal.style.display = 'none');
    document.getElementById('enableAiBtn').addEventListener('click', handleAiAuthentication);
    
    // Password input for AI
    passwordInput.addEventListener('input', handlePasswordInput);
    
    // Account
    document.getElementById('logoutBtn').addEventListener('click', logout);
    
    // Complaint modal
    document.getElementById('closeComplaintModal').addEventListener('click', () => complaintModal.style.display = 'none');
    
    // Agent Name modal
    document.getElementById('closeAgentNameModal').addEventListener('click', () => agentNameModal.style.display = 'none');
    document.getElementById('cancelAgentNameBtn').addEventListener('click', () => agentNameModal.style.display = 'none');
    document.getElementById('generateBotBtn').addEventListener('click', startChatWithAgent);
    
    // Agent name input enter key
    agentNameInput.addEventListener('keypress', function(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            startChatWithAgent();
        }
    });
    
    // Evaluation modal
    closeCaseBtn.addEventListener('click', showEvaluation);
    document.getElementById('closeEvaluationModal').addEventListener('click', () => evaluationModal.style.display = 'none');
    document.getElementById('saveEvaluationBtn').addEventListener('click', saveEvaluation);
    
    // Emotion panel expand button
    document.querySelector('.expand-btn').addEventListener('click', function() {
        const emotionText = document.querySelector('.emotion-text');
        emotionText.style.display = emotionText.style.display === 'none' ? 'block' : 'none';
    });
    
    // Update emotion title when reform changes
    reformSelect.addEventListener('change', function() {
        emotionTitle.textContent = `Emotion Reform : ${reformSelect.value}`;
    });
    
    // Configuration collapse/expand
    configCollapseBtn.addEventListener('click', toggleConfigCollapse);
    
    // Team sidebar collapse/expand
    teamCollapseBtn.addEventListener('click', toggleTeamCollapse);
    
    // Close modals when clicking outside
    window.addEventListener('click', function(event) {
        if (event.target.classList.contains('modal')) {
            event.target.style.display = 'none';
        }
    });
}

async function handleDomainChange() {
    const selectedDomain = domainSelect.value;
    
    if (!selectedDomain) {
        jobPositionSelect.disabled = true;
        jobPositionSelect.innerHTML = '<option value="">Select Job Position...</option>';
        detailBtn.disabled = true;
        startBtn.disabled = true;
        return;
    }
    
    try {
        const response = await fetch(`/api/job-positions/${encodeURIComponent(selectedDomain)}`);
        const data = await response.json();
        
        jobPositionSelect.innerHTML = '<option value="">Select Job Position...</option>';
        data.job_positions.forEach(position => {
            const option = document.createElement('option');
            option.value = position;
            option.textContent = position;
            jobPositionSelect.appendChild(option);
        });
        
        jobPositionSelect.disabled = false;
    } catch (error) {
        console.error('Error loading job positions:', error);
        showNotification('Error loading job positions', 'error');
    }
}

function handleJobPositionChange() {
    const selectedDomain = domainSelect.value;
    const selectedPosition = jobPositionSelect.value;
    
    if (selectedDomain && selectedPosition) {
        detailBtn.disabled = false;
        startBtn.disabled = false;
    } else {
        detailBtn.disabled = true;
        startBtn.disabled = true;
    }
}

async function showComplaintDetail() {
    const selectedDomain = domainSelect.value;
    const selectedPosition = jobPositionSelect.value;
    
    if (!selectedDomain || !selectedPosition) return;
    
    try {
        const response = await fetch(`/api/complaint-detail/${encodeURIComponent(selectedDomain)}/${encodeURIComponent(selectedPosition)}`);
        const data = await response.json();
        
        const detailHtml = `
            <div class="complaint-detail">
                <p><strong>Domain:</strong> ${data.domain}</p>
                <p><strong>Job Position:</strong> ${data.job_position}</p>
                <p><strong>Category:</strong> ${data.category}</p>
                <p><strong>Complaint:</strong> ${data.complaint}</p>
            </div>
        `;
        
        document.getElementById('complaintDetail').innerHTML = detailHtml;
        complaintModal.style.display = 'block';
    } catch (error) {
        console.error('Error loading complaint detail:', error);
        showNotification('Error loading complaint detail', 'error');
    }
}

function showAgentNameModal() {
    // Clear previous input
    agentNameInput.value = '';
    // Show the modal
    agentNameModal.style.display = 'block';
    // Focus on the input
    agentNameInput.focus();
}

async function startChatWithAgent() {
    const agentName = agentNameInput.value.trim();
    
    if (!agentName) {
        showNotification('Please enter an agent name', 'error');
        agentNameInput.focus();
        return;
    }
    
    // Store agent name
    currentAgentName = agentName;
    
    // Hide agent name modal
    agentNameModal.style.display = 'none';
    
    // Show loading modal
    showLoadingModal('Generating Bot...', 'Please wait while we initialize your chatbot conversation.');
    
    try {
        // Create new session
        const sessionResponse = await fetch('/api/sessions', { method: 'POST' });
        const sessionData = await sessionResponse.json();
        currentSessionId = sessionData.session_id;
        
        // Update configuration
        const config = {
            language: languageSelect.value,
            domain: domainSelect.value,
            job_position: jobPositionSelect.value,
            difficulty: difficultySelect.value,
            reform: reformSelect.value,
            agent_name: currentAgentName
        };
        
        const configResponse = await fetch(`/api/sessions/${currentSessionId}/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        
        if (!configResponse.ok) {
            throw new Error('Failed to update configuration');
        }
        
        // Enable AI if needed
        if (isAiEnabled) {
            await fetch(`/api/sessions/${currentSessionId}/ai-enable?enabled=true`, { method: 'PUT' });
        }
        
        // Start the session
        const startResponse = await fetch(`/api/sessions/${currentSessionId}/start`, { method: 'POST' });
        const startData = await startResponse.json();
        
        // Clear chat and add initial messages
        chatMessages.innerHTML = '';
        startData.messages.forEach(message => {
            addMessageToChat(message);
        });
        
        // Update UI state
        chatStarted = true;
        startBtn.style.display = 'none';
        restartBtn.style.display = 'block';
        chatInput.disabled = false;
        sendBtn.disabled = false;
        
        // Update AI features based on current AI state
        toggleAiFeatures(isAiEnabled);
        
        if (isAiEnabled) {
            updateSentimentAndSuggestions();
        }
        
        currentConfig = config;
        
        // Hide loading modal
        hideLoadingModal();
        showNotification('Chat started successfully!', 'success');
        
    } catch (error) {
        // Hide loading modal in case of error
        hideLoadingModal();
        
        console.error('Error starting chat:', error);
        showNotification('Error starting chat', 'error');
    }
}

function restartChat() {
    // Reset UI state
    chatStarted = false;
    currentSessionId = null;
    currentAgentName = null;
    startBtn.style.display = 'block';
    restartBtn.style.display = 'none';
    chatInput.disabled = true;
    sendBtn.disabled = true;
    
    // Reset AI features to current AI state
    toggleAiFeatures(isAiEnabled);
    
    // Clear chat messages
    chatMessages.innerHTML = '<div class="welcome-message"><p>Configure the bot settings and click "Generate Bot" to start the conversation.</p></div>';
    
    // Hide suggestions panel
    suggestionsPanel.style.display = 'none';
    
    // Reset sentiment
    sentimentSlider.value = 0;
    sentimentValue.textContent = '0.0';
    
    // Reset Close Case button to disabled state since chat hasn't started
    closeCaseBtn.disabled = true;
    closeCaseBtn.classList.remove('green');
    
    showNotification('Chat restarted', 'info');
}

function handleChatInputKeypress(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

async function sendMessage() {
    const message = chatInput.value.trim();
    if (!message || !currentSessionId) return;
    
    try {
        // Add user message to chat
        const userMessage = {
            role: 'user',
            content: message,
            timestamp: new Date().toISOString()
        };
        
        addMessageToChat(userMessage);
        chatInput.value = '';
        
        // Send message to API
        const response = await fetch(`/api/sessions/${currentSessionId}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(userMessage)
        });
        
        const data = await response.json();
        
        // Add bot messages to chat
        data.messages.forEach(message => {
            addMessageToChat(message);
        });
        
        // Update sentiment and suggestions if AI is enabled
        if (isAiEnabled) {
            updateSentiment(data.sentiment_score);
            updateSuggestions(data.suggestions);
            updateEmotionAdvice(data.emotion_advice);
        }
        
    } catch (error) {
        console.error('Error sending message:', error);
        showNotification('Error sending message', 'error');
    }
}

function addMessageToChat(message) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.role}`;
    
    const icon = message.role === 'user' ? '👨‍💻' : '👺';
    const name = message.role === 'user' ? 'user' : (message.name || 'Chatbot');
    
    messageDiv.innerHTML = `
        <div class="message-header">
            <span class="icon">${icon}</span>
            <strong>${name}</strong>
        </div>
        <div class="message-content">${message.content}</div>
    `;
    
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function handlePasswordInput() {
    const password = passwordInput.value;
    
    // We don't show/hide controls based on password anymore
    // Just validate that there's a password entered
    if (password.length > 0) {
        // Password entered, ready to authenticate
    }
}

async function handleAiAuthentication() {
    const password = passwordInput.value;
    
    if (!password) {
        showNotification('Please enter a password', 'error');
        passwordInput.focus();
        return;
    }
    
    try {
        const response = await fetch('/api/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.authenticated) {
                // Enable AI directly
                isAiEnabled = true;
                
                // Update AI status in header
                aiStatus.textContent = `Enable AI : ON`;
                
                // Enable/disable AI UI elements
                toggleAiFeatures(true);
                
                // Enable AI for current session if exists
                if (currentSessionId) {
                    try {
                        await fetch(`/api/sessions/${currentSessionId}/ai-enable?enabled=true`, { method: 'PUT' });
                        
                        if (chatStarted) {
                            updateSentimentAndSuggestions();
                        }
                    } catch (error) {
                        console.error('Error updating AI status:', error);
                    }
                }
                
                // Hide modal and show success
                aiConfigModal.style.display = 'none';
                showNotification('AI enabled successfully!', 'success');
                
                // Clear password for security
                passwordInput.value = '';
            }
        } else {
            showNotification('Invalid password', 'error');
        }
    } catch (error) {
        console.error('Authentication error:', error);
        showNotification('Authentication failed', 'error');
    }
}


function toggleAiFeatures(enabled) {
    // Get emotion reform panel elements
    const emotionPanel = document.querySelector('.emotion-panel');
    const sentimentPanel = document.querySelector('.sentiment-panel');
    const leftPanel = document.querySelector('.left-panel');
    const rightPanel = document.querySelector('.right-panel');
    
    if (enabled) {
        // Show emotion reform panel
        if (emotionPanel) {
            emotionPanel.style.display = 'block';
        }
        if (leftPanel) {
            leftPanel.style.display = 'block';
        }
        
        // Show sentiment panel
        if (sentimentPanel) {
            sentimentPanel.style.display = 'block';
        }
        if (rightPanel) {
            rightPanel.style.display = 'block';
        }
        
        // Enable interactive elements
        sentimentSlider.disabled = false;
        if (chatStarted) {
            closeCaseBtn.disabled = false;
        }
        
    } else {
        // Hide emotion reform panel when AI is OFF
        if (emotionPanel) {
            emotionPanel.style.display = 'none';
        }
        if (leftPanel) {
            leftPanel.style.display = 'none';
        }
        
        // Keep sentiment panel visible but modify its behavior
        if (sentimentPanel) {
            sentimentPanel.style.display = 'block';
        }
        if (rightPanel) {
            rightPanel.style.display = 'block';
        }
        
        // Disable sentiment slider but keep Close Case button enabled and green
        sentimentSlider.disabled = true;
        if (chatStarted) {
            closeCaseBtn.disabled = false;
            closeCaseBtn.classList.add('green');
        } else {
            closeCaseBtn.disabled = true;
            closeCaseBtn.classList.remove('green');
        }
        
        // Hide suggestions panel when AI is OFF
        suggestionsPanel.style.display = 'none';
        
        // Reset sentiment display
        sentimentSlider.value = 0;
        sentimentValue.textContent = '0.0';
    }
}

async function updateSentimentAndSuggestions() {
    if (!currentSessionId || !isAiEnabled) return;
    
    try {
        const response = await fetch(`/api/sessions/${currentSessionId}/sentiment`);
        const data = await response.json();
        updateSentiment(data.sentiment_score);
    } catch (error) {
        console.error('Error updating sentiment:', error);
    }
}

function updateSentiment(score) {
    sentimentSlider.value = score;
    sentimentValue.textContent = score.toFixed(1);
    
    // Only apply AI-based sentiment logic when AI is enabled
    if (isAiEnabled) {
        // Enable close case button and make it green if sentiment is low (< 20)
        if (score < 20) {
            closeCaseBtn.disabled = false;
            closeCaseBtn.classList.add('green');
        } else {
            closeCaseBtn.classList.remove('green');
            closeCaseBtn.disabled = true;
        }
    }
    // When AI is disabled, the button state is controlled by toggleAiFeatures function
    // and should remain enabled and green if chat has started
}

function updateSuggestions(suggestions) {
    if (!suggestions || suggestions.length === 0) {
        suggestionsPanel.style.display = 'none';
        return;
    }

    suggestionsList.innerHTML = '';
    suggestions.forEach((suggestion, index) => {
        const button = document.createElement('button');
        button.className = 'suggestion-pill clickable';
        button.textContent = `📝 ${suggestion}`;
        button.disabled = false;
        button.title = 'Click to use this suggestion';
        
        // Add click event to fill the input
        button.addEventListener('click', function() {
            chatInput.value = suggestion;
            chatInput.focus();
            // Auto-scroll to chat input
            chatInput.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
        
        suggestionsList.appendChild(button);
    });

    suggestionsPanel.style.display = 'block';
}

function updateEmotionAdvice(advice) {
    if (advice) {
        emotionText.textContent = advice;
    }
}

function showLoadingModal(title = 'Processing...', message = 'Please wait while we process your request.') {
    loadingTitle.textContent = title;
    loadingMessage.textContent = message;
    loadingModal.style.display = 'block';
}

function hideLoadingModal() {
    loadingModal.style.display = 'none';
}

async function showEvaluation() {
    if (!currentSessionId) return;
    
    // Show loading modal
    showLoadingModal('Processing Evaluation...', 'Please wait while we evaluate your conversation.');
    
    try {
        const response = await fetch(`/api/sessions/${currentSessionId}/evaluate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSessionId })
        });
        
        // Hide loading modal
        hideLoadingModal();
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        const evaluation = data.evaluation;
        
        // Populate evaluation modal
        document.getElementById('evalAgentName').textContent = evaluation.agent_name || currentAgentName || 'Unknown';
        document.getElementById('evalDateTime').textContent = evaluation.evaluation_date;
        document.getElementById('evalTotalTime').textContent = evaluation.total_time_minutes.toFixed(2);
        document.getElementById('evalComment').textContent = evaluation.comment;
        
        // Populate scores
        const scoresHtml = `
            <div class="evaluation-score-item">
                <span>1. Response Accuracy :</span>
                ${generateStars(evaluation.scores.response_accuracy)}
            </div>
            <div class="evaluation-score-item">
                <span>2. Response Time :</span>
                ${generateStars(evaluation.scores.response_time)}
            </div>
            <div class="evaluation-score-item">
                <span>3. Empathy and Tone :</span>
                ${generateStars(evaluation.scores.empathy_and_tone)}
            </div>
            <div class="evaluation-score-item">
                <span>4. Clarity :</span>
                ${generateStars(evaluation.scores.clarity)}
            </div>
            <div class="evaluation-score-item">
                <span>5. Professionalism :</span>
                ${generateStars(evaluation.scores.professionalism)}
            </div>
        `;
        
        document.getElementById('evaluationScores').innerHTML = scoresHtml;
        evaluationModal.style.display = 'block';
        
    } catch (error) {
        // Hide loading modal in case of error
        hideLoadingModal();
        
        console.error('Error showing evaluation:', error);
        showNotification('Error generating evaluation: ' + error.message, 'error');
    }
}

function generateStars(rating) {
    const stars = '★'.repeat(rating);
    return `<span class="star">${stars}</span>`;
}

function saveEvaluation() {
    evaluationModal.style.display = 'none';
    showNotification('Evaluation saved successfully!', 'success');
    restartChat();
}

function toggleConfigCollapse() {
    const isCollapsed = configContent.classList.contains('collapsed');
    
    if (isCollapsed) {
        // Expand
        configContent.classList.remove('collapsed');
        configCollapseBtn.classList.remove('collapsed');
        configHeader.classList.remove('collapsed');
        sidebar.classList.remove('collapsed');
        // Store the expanded state
        localStorage.setItem('configCollapsed', 'false');
    } else {
        // Collapse
        configContent.classList.add('collapsed');
        configCollapseBtn.classList.add('collapsed');
        configHeader.classList.add('collapsed');
        sidebar.classList.add('collapsed');
        // Store the collapsed state
        localStorage.setItem('configCollapsed', 'true');
    }
}

function initializeConfigCollapse() {
    // Check if there's a stored preference
    const isCollapsed = localStorage.getItem('configCollapsed') === 'true';
    
    if (isCollapsed) {
        configContent.classList.add('collapsed');
        configCollapseBtn.classList.add('collapsed');
        configHeader.classList.add('collapsed');
        sidebar.classList.add('collapsed');
    }
}

function toggleTeamCollapse() {
    const isCollapsed = teamContent.classList.contains('collapsed');
    
    if (isCollapsed) {
        // Expand
        teamContent.classList.remove('collapsed');
        teamCollapseBtn.classList.remove('collapsed');
        teamHeader.classList.remove('collapsed');
        teamSidebar.classList.remove('collapsed');
        // Store the expanded state
        localStorage.setItem('teamCollapsed', 'false');
    } else {
        // Collapse
        teamContent.classList.add('collapsed');
        teamCollapseBtn.classList.add('collapsed');
        teamHeader.classList.add('collapsed');
        teamSidebar.classList.add('collapsed');
        // Store the collapsed state
        localStorage.setItem('teamCollapsed', 'true');
    }
}

function initializeTeamCollapse() {
    // Check if there's a stored preference
    const isCollapsed = localStorage.getItem('teamCollapsed') === 'true';
    
    if (isCollapsed) {
        teamContent.classList.add('collapsed');
        teamCollapseBtn.classList.add('collapsed');
        teamHeader.classList.add('collapsed');
        teamSidebar.classList.add('collapsed');
    }
}

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    // Style the notification
    Object.assign(notification.style, {
        position: 'fixed',
        top: '20px',
        right: '20px',
        padding: '1rem 1.5rem',
        borderRadius: '0.375rem',
        color: 'white',
        fontWeight: '500',
        zIndex: '1001',
        opacity: '0',
        transition: 'opacity 0.3s ease'
    });
    
    // Set background color based on type
    const colors = {
        success: '#28a745',
        error: '#dc3545',
        info: '#17a2b8',
        warning: '#ffc107'
    };
    
    notification.style.backgroundColor = colors[type] || colors.info;
    
    // Add to DOM and animate
    document.body.appendChild(notification);
    setTimeout(() => notification.style.opacity = '1', 10);
    
    // Remove after 3 seconds
    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => document.body.removeChild(notification), 300);
    }, 3000);
}
