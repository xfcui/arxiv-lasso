const LOG_PREFIX = '[Journal Scraper: Popup]';

// --- UI Elements ---
const elements = {
  startBtn: document.getElementById('startBtn'),
  stopBtn: document.getElementById('stopBtn'),
  statusText: document.getElementById('status'),
  statsArea: document.getElementById('statsArea'),
  startSpinner: document.getElementById('startSpinner'),
  lastArticle: document.getElementById('lastArticle'),
  lastArticleTitle: document.getElementById('lastArticleTitle'),
  journalBadge: document.getElementById('journalBadge'),
  issueCount: document.getElementById('issueCount'),
  articleCount: document.getElementById('articleCount'),
  issueLabel: document.getElementById('issueLabel')
};

// --- UI Updates ---
function updateUI(status) {
  console.log(`${LOG_PREFIX} UI Update:`, status);
  
  const isScraping = status.isScraping;
  
  // Visibility and state
  elements.startBtn.disabled = isScraping;
  elements.startSpinner.style.display = isScraping ? 'inline-block' : 'none';
  elements.stopBtn.style.display = isScraping ? 'block' : 'none';
  elements.statsArea.style.display = isScraping ? 'flex' : 'none';
  elements.lastArticle.style.display = (isScraping && status.lastScrapedTitle) ? 'block' : 'none';
  
  // Text updates
  elements.statusText.innerText = isScraping ? 'Extracting articles...' : 'Ready';
  elements.statusText.style.color = '#343a40';
  
  if (isScraping) {
    elements.issueCount.innerText = status.issueCount || 0;
    elements.articleCount.innerText = status.articleCount || 0;
    
    // Journal badge
    if (status.journal) {
      elements.journalBadge.style.display = 'inline-block';
      elements.journalBadge.innerText = status.journal;
      elements.journalBadge.className = `journal-badge journal-${status.journal.toLowerCase()}`;
      
      // Label context
      elements.issueLabel.innerText = (status.journal === 'nature') ? 'Pages' : 'Issues';
    } else {
      elements.journalBadge.style.display = 'none';
    }
    
    if (status.lastScrapedTitle) {
      elements.lastArticleTitle.innerText = status.lastScrapedTitle;
    }
  } else {
    elements.journalBadge.style.display = 'none';
  }
}

// --- Initialization ---
function init() {
  console.log(`${LOG_PREFIX} Popup opened, checking status`);
  chrome.runtime.sendMessage({ action: 'checkStatus' }, (response) => {
    if (response) {
      console.log(`${LOG_PREFIX} Initial status received:`, response);
      updateUI(response);
    }
  });
}

// --- Event Listeners ---
chrome.runtime.onMessage.addListener((message) => {
  if (message.action === 'statusUpdate') {
    console.log(`${LOG_PREFIX} Received status update from background`);
    updateUI(message);
  }
});

elements.startBtn.addEventListener('click', () => {
  console.log(`${LOG_PREFIX} Start button clicked`);
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const activeTab = tabs[0];
    if (!activeTab?.url) return;

    const url = activeTab.url;
    const isCell = /cell\.com\/[^/]+\/issue/.test(url);
    const isScience = /science\.org\/toc\//.test(url);
    const isNatureSearch = url.includes('nature.com/search');
    const isNatureIssue = url.includes('nature.com/') && url.includes('/volumes/') && url.includes('/issues/');

    if (isCell || isScience || isNatureSearch || isNatureIssue) {
      console.log(`${LOG_PREFIX} Valid journal page: ${url}`);
      let journal = 'science';
      if (isNatureSearch || isNatureIssue) journal = 'nature';
      if (isCell) journal = 'cell';
      
      chrome.runtime.sendMessage({ action: 'startScraping', url, journal });
      updateUI({ isScraping: true, issueCount: 0, articleCount: 0, journal });
    } else {
      console.warn(`${LOG_PREFIX} Invalid page: ${url}`);
      elements.statusText.innerText = 'Please open a journal TOC page (e.g., cell.com/.../issue or science.org/toc/...).';
      elements.statusText.style.color = '#dc3545';
    }
  });
});

elements.stopBtn.addEventListener('click', () => {
  console.log(`${LOG_PREFIX} Stop button clicked`);
  chrome.runtime.sendMessage({ action: 'stopScraping' });
  updateUI({ isScraping: false });
});

// Start
init();
