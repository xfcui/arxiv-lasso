const LOG_PREFIX = '[Journal Scraper: Background]';

// --- State Management ---
const state = {
  isScraping: false,
  scraperTabId: null,
  currentJournal: null,
  allArticles: [],
  natureArticles: [],
  naturePageCount: 0,
  issueCount: 0,
  lastScrapedTitle: '',
};

function resetState(journal = null) {
  state.isScraping = true;
  state.currentJournal = journal;
  state.allArticles = [];
  state.natureArticles = [];
  state.naturePageCount = 0;
  state.issueCount = 0;
  state.lastScrapedTitle = '';
}

// --- Communication ---
function broadcastStatus() {
  console.log(`${LOG_PREFIX} Status: ${state.allArticles.length} articles, ${state.issueCount} issues, ${state.naturePageCount} nature pages`);
  chrome.runtime.sendMessage({
    action: 'statusUpdate',
    isScraping: state.isScraping,
    journal: state.currentJournal,
    issueCount: state.naturePageCount || state.issueCount,
    articleCount: state.natureArticles.length || state.allArticles.length,
    lastScrapedTitle: state.lastScrapedTitle
  }).catch(() => {}); // Ignore errors when popup is closed
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log(`${LOG_PREFIX} Message:`, message.action);
  
  switch (message.action) {
    case 'startScraping':
      handleStartScraping(message, sendResponse);
      break;
    case 'stopScraping':
      handleStopScraping(sendResponse);
      break;
    case 'checkStatus':
      handleCheckStatus(sendResponse);
      break;
    case 'pageScraped':
      handlePageScraped(message, sendResponse);
      break;
  }
  return true;
});

// --- Action Handlers ---
function handleStartScraping(message, sendResponse) {
  console.log(`${LOG_PREFIX} Starting extraction: ${message.url}`);
  resetState(message.journal);
  
  chrome.tabs.create({ url: message.url, active: false }, (tab) => {
    state.scraperTabId = tab.id;
    console.log(`${LOG_PREFIX} Tab created: ${state.scraperTabId}`);
  });
  sendResponse({ status: 'started' });
}

function handleStopScraping(sendResponse) {
  console.log(`${LOG_PREFIX} Stopping extraction (manual)`);
  stopScraping(true); // Close tab only on manual stop
  sendResponse({ status: 'stopped' });
}

function handleCheckStatus(sendResponse) {
  sendResponse({ 
    isScraping: state.isScraping, 
    issueCount: state.naturePageCount || state.issueCount, 
    articleCount: state.natureArticles.length || state.allArticles.length,
    lastScrapedTitle: state.lastScrapedTitle,
    journal: state.currentJournal
  });
}

function handlePageScraped(message, sendResponse) {
  console.log(`${LOG_PREFIX} Data received`);
  handleScrapedData(message.data);
  sendResponse({ status: 'received' });
}

// --- Data Processing ---
function handleScrapedData(data) {
  if (!state.isScraping) {
    console.log(`${LOG_PREFIX} Extraction inactive, ignoring data`);
    return;
  }

  if (data.isNatureSearch) {
    handleNatureSearchData(data);
    return;
  }

  // If this is just a heartbeat for saving HTML, handle it and return
  if (data.isHeartbeat) {
    console.log(`${LOG_PREFIX} Heartbeat received, saving HTML for debugging`);
    saveHtmlForDebug(data);
    return;
  }

  state.issueCount++;
  state.allArticles = state.allArticles.concat(data.articles);
  if (data.articles.length > 0) {
    state.lastScrapedTitle = data.articles[data.articles.length - 1].title;
  }
  console.log(`${LOG_PREFIX} Extracted ${data.articles.length} articles from ${data.journal}`);
  broadcastStatus();

  // 1. Save page manifest in unified "issue" format
  const manifest = {
    type: 'issue',
    journal: data.journal,
    publicationDate: data.publicationDate,
    scrapedAt: new Date().toISOString(),
    articles: data.articles
  };
  
  const yyyymmdd = parsePublicationDate(data.publicationDate);
  const yyyy = yyyymmdd.substring(0, 4);
  const mmdd = yyyymmdd.substring(4);
  const manifestFilename = `chrome/${data.journal}/${yyyy}/${mmdd}.json`;
  console.log(`${LOG_PREFIX} Saving: ${manifestFilename}`);
  saveFile(JSON.stringify(manifest, null, 2), manifestFilename, 'application/json');

  // 2. Save page HTML for debugging
  saveHtmlForDebug(data);

  // 3. Navigate to next issue or finish with delay
  if (data.nextIssueUrl) {
    console.log(`${LOG_PREFIX} Next issue in 2s: ${data.nextIssueUrl}`);
    setTimeout(() => {
      if (state.isScraping && state.scraperTabId) {
        chrome.tabs.update(state.scraperTabId, { url: data.nextIssueUrl }, (tab) => {
          if (chrome.runtime.lastError) {
            console.error(`${LOG_PREFIX} Navigation failed:`, chrome.runtime.lastError);
            stopScraping();
          } else {
            console.log(`${LOG_PREFIX} Navigating tab ${state.scraperTabId}`);
          }
        });
      }
    }, 2000);
  } else {
    console.log(`${LOG_PREFIX} End of issues reached`);
    stopScraping();
  }
}

function handleNatureSearchData(data) {
  state.naturePageCount++;
  console.log(`${LOG_PREFIX} Nature Search Page ${state.naturePageCount} scraped`);

  // Accumulate all articles
  state.natureArticles = state.natureArticles.concat(data.articles);

  if (data.articles.length > 0) {
    state.lastScrapedTitle = data.articles[data.articles.length - 1].title;
  }
  broadcastStatus();

  // Save HTML for this page
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  const yyyymmdd = `${yyyy}${mm}${dd}`;
  
  const pageNum = String(state.naturePageCount).padStart(2, '0');
  const baseFilename = `chrome/search/${yyyymmdd}/page${pageNum}`;
  
  saveFile(data.htmlContent, `${baseFilename}.html`, 'text/html');
  
  // Save JSON for this page
  const pageData = {
    type: 'search_page',
    scrapedAt: now.toISOString(),
    articles: data.articles
  };
  saveFile(JSON.stringify(pageData, null, 2), `${baseFilename}.json`, 'application/json');

  // Handle pagination: stop at last page or 20th page
  const pagination = data.pagination || {};
  const totalResults = pagination.totalResults || 0;
  const stopAtPage = Math.min(Math.ceil(totalResults / 50), 20);

  console.log(`${LOG_PREFIX} Pagination: ${totalResults} results, 50 per page -> ${stopAtPage} max pages (limit 20). Current page: ${state.naturePageCount}, Stop at: ${stopAtPage}`);

  if (state.naturePageCount < stopAtPage) {
    const baseUrl = data.currentUrl.split('&page=')[0];
    const nextUrl = `${baseUrl}&page=${state.naturePageCount + 1}`;
    console.log(`${LOG_PREFIX} Navigating to Nature Search Page ${state.naturePageCount + 1} of ${stopAtPage}: ${nextUrl}`);
    
    setTimeout(() => {
      if (state.isScraping && state.scraperTabId) {
        chrome.tabs.update(state.scraperTabId, { url: nextUrl });
      }
    }, 2000);
  } else {
    console.log(`${LOG_PREFIX} Reached limit (${state.naturePageCount} pages) of Nature Search`);
    stopScraping();
  }
}

// --- Utilities ---
function parsePublicationDate(pubDate) {
  const date = new Date(pubDate);
  if (!isNaN(date.getTime())) {
    const yyyy = date.getFullYear();
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const dd = String(date.getDate()).padStart(2, '0');
    return `${yyyy}${mm}${dd}`;
  } else {
    const yearMatch = pubDate.match(/\b(20\d{2})\b/);
    const year = yearMatch ? yearMatch[1] : 'unknown_year';
    return `${year}_unknown`;
  }
}

function saveHtmlForDebug(data) {
  const yyyymmdd = parsePublicationDate(data.publicationDate);
  const yyyy = yyyymmdd.substring(0, 4);
  const mmdd = yyyymmdd.substring(4);
  const htmlFilename = `chrome/${data.journal}/${yyyy}/${mmdd}.html`;
  console.log(`${LOG_PREFIX} Saving HTML: ${htmlFilename}`);
  saveFile(data.htmlContent, htmlFilename, 'text/html');
}

function saveFile(content, filename, type) {
  chrome.downloads.setShelfEnabled(false);
  
  const blob = new Blob([content], { type });
  const reader = new FileReader();
  reader.onload = function() {
    chrome.downloads.download({
      url: reader.result,
      filename: filename,
      saveAs: false
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        console.error(`${LOG_PREFIX} Save failed (${filename}):`, chrome.runtime.lastError);
      } else {
        console.log(`${LOG_PREFIX} Saved: ${filename}`);
      }
    });
  };
  reader.readAsDataURL(blob);
}

function stopScraping(shouldCloseTab = false) {
  state.isScraping = false;
  if (state.scraperTabId && shouldCloseTab) {
    console.log(`${LOG_PREFIX} Closing tab: ${state.scraperTabId}`);
    chrome.tabs.remove(state.scraperTabId, () => {
      state.scraperTabId = null;
    });
  } else if (state.scraperTabId) {
    console.log(`${LOG_PREFIX} Keeping tab open: ${state.scraperTabId}`);
    state.scraperTabId = null;
  }
  broadcastStatus();
}
