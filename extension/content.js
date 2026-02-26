(function() {
  const LOG_PREFIX = '[Journal Scraper: Content]';
  console.log(`${LOG_PREFIX} Script loaded`);

  // --- Initialization ---
  chrome.runtime.sendMessage({ action: 'checkStatus' }, (response) => {
    if (response && response.isScraping) {
      console.log(`${LOG_PREFIX} Extraction active, starting...`);
      scrapePage();
    }
  });

  function scrapePage() {
    const url = window.location.href;
    const hostname = window.location.hostname;
    console.log(`${LOG_PREFIX} Scraping: ${url}`);

    if (hostname.includes('science.org')) {
      scrapeSciencePage();
    } else if (hostname.includes('cell.com')) {
      scrapeCellPage();
    } else if (hostname.includes('nature.com')) {
      if (url.includes('/search')) {
        scrapeNatureSearchPage();
      } else if (url.includes('/volumes/') && url.includes('/issues/')) {
        scrapeNatureIssuePage();
      }
    } else {
      console.error(`${LOG_PREFIX} Unsupported journal: ${hostname}`);
    }
  }

  // --- Scrapers ---

  function scrapeNatureSearchPage() {
    console.log(`${LOG_PREFIX} Scraping Nature Search...`);
    
    const journalName = extractNatureJournalName();
    const journalCode = extractNatureJournalCode();
    const { totalResults, pageSize } = extractNaturePagination();
    
    console.log(`${LOG_PREFIX} Journal: ${journalName} (${journalCode}), Total results: ${totalResults}, Page size: ${pageSize}`);

    const articleContainers = document.querySelectorAll('article.c-card, li[data-test="article-item"]');
    const articles = Array.from(articleContainers).map(container => {
      const titleEl = container.querySelector('a[data-test="article-title"], a.c-card__link');
      const dateEl = container.querySelector('time[itemprop="datePublished"]');
      const journalEl = container.querySelector('[data-test="journal-title-and-link"], .c-meta__item.u-text-bold');
      const oaEl = container.querySelector('[data-test="open-access"]');
      const typeEl = container.querySelector('[data-test="article-type"], [data-test="article.type"]');
      
      const title = titleEl?.textContent.trim() || 'Unknown Title';
      const url = titleEl?.href || null;
      const journal = journalEl?.textContent.trim() || journalName;
      const publicationDate = dateEl?.getAttribute('datetime') || dateEl?.textContent.trim() || 'Unknown Date';
      const isOA = !!oaEl;
      const type = typeEl?.textContent.trim() || '';

      console.log(`${LOG_PREFIX} Found element:`, { title, type });
      
      return {
        title,
        url,
        journal,
        publicationDate,
        isOA,
        type
      };
    }).filter(art => {
      const isMatch = art.url && (
        art.type.toLowerCase() === 'article' || 
        art.type.toLowerCase() === 'research' ||
        art.type === '' // Fallback if type is missing but it's a card
      );
      if (!isMatch) {
        console.log(`${LOG_PREFIX} Filtering out: ${art.title} (type: ${art.type})`);
      }
      return isMatch;
    });

    console.log(`${LOG_PREFIX} Found ${articles.length} articles`);

    sendDataToBackground({
      articles: articles.map(({ type, ...rest }) => rest),
      isNatureSearch: true,
      journal: journalName,
      currentUrl: window.location.href,
      pagination: {
        totalResults,
        pageSize,
        maxPages: Math.ceil(totalResults / pageSize)
      }
    });
  }

  function scrapeNatureIssuePage() {
    console.log(`${LOG_PREFIX} Scraping Nature Issue...`);
    
    const journal = extractNatureJournalName() || window.location.pathname.split('/').filter(Boolean)[0] || 'nature';
    const issueHeader = document.querySelector('h1[data-container-type="title"]') || document.querySelector('h1.c-issue-header__title') || document.querySelector('h1');
    let publicationDate = issueHeader?.textContent.trim() || 'Unknown Date';

    // Normalize date from "Volume 23 Issue 1, January 2022" to "01 January 2022"
    if (publicationDate.includes('Issue')) {
      const monthYearMatch = publicationDate.match(/,\s+([A-Za-z]+\s+\d{4})/);
      if (monthYearMatch) {
        publicationDate = `01 ${monthYearMatch[1]}`;
      }
    }

    console.log(`${LOG_PREFIX} Journal: ${journal}, Publication Date: ${publicationDate}`);

    const sectionHeaders = Array.from(document.querySelectorAll('h3.c-section-heading, div.c-section-heading'));
    let articles = [];

    sectionHeaders.forEach((header) => {
      const sectionName = header.textContent.trim();

      // Skip "Reviews" or "Review Articles" sections
      if (sectionName.toLowerCase().includes('review')) {
        console.log(`${LOG_PREFIX} Skipping review section: ${sectionName}`);
        return;
      }

      let currentEl = header.nextElementSibling;
      let sectionArticles = [];

      // Handle both <h3> and <div> containers for section headings
      while (currentEl && !currentEl.matches('h3.c-section-heading, div.c-section-heading')) {
        const articleElements = Array.from(currentEl.querySelectorAll('li.app-article-list-row__item'));
        articleElements.forEach(el => {
          const titleEl = el.querySelector('h3.c-card__title a');
          const oaEl = el.querySelector('.u-color-open-access');

          if (titleEl) {
            sectionArticles.push({
              title: titleEl.textContent.trim(),
              url: titleEl.href,
              section: sectionName,
              isOA: !!oaEl
            });
          }
        });
        currentEl = currentEl.nextElementSibling;
      }

      // Filter for "Articles" section (strictly "Articles", excluding "Review Articles")
      const filtered = sectionArticles.filter(art => art.section.trim() === 'Articles');
      articles = articles.concat(filtered.map(({ section, ...rest }) => rest));
    });

    console.log(`${LOG_PREFIX} Total articles: ${articles.length}`);
    const nextIssueUrl = findNextIssueUrlNature();
    sendDataToBackground({ articles, journal, publicationDate, nextIssueUrl });
  }

  function scrapeSciencePage() {
    console.log(`${LOG_PREFIX} Scraping Science...`);
    
    const journal = extractScienceJournalName();
    
    const issueVolEl = document.querySelector('.journal-issue__vol');
    let publicationDate = 'Unknown Date';
    if (issueVolEl) {
      const parts = Array.from(issueVolEl.querySelectorAll('li')).map(li => li.textContent.replace(/\|/g, '').trim());
      if (parts.length > 0) publicationDate = parts[parts.length - 1];
    }

    console.log(`${LOG_PREFIX} Journal: ${journal}, Publication Date: ${publicationDate}`);

    const sectionHeaders = Array.from(document.querySelectorAll('h4.to-section, h5.to-section'));
    let articles = [];

    sectionHeaders.forEach((header) => {
      const sectionName = header.textContent.trim();

      // Skip "Reviews" or "Review Articles" sections
      if (sectionName.toLowerCase().includes('review')) {
        console.log(`${LOG_PREFIX} Skipping review section: ${sectionName}`);
        return;
      }

      let currentEl = header.nextElementSibling;
      let sectionArticles = [];

      while (currentEl && !currentEl.matches('h4.to-section, h5.to-section')) {
        const articleLinks = Array.from(currentEl.querySelectorAll('a[href*="/doi/abs/"]'));
        articleLinks.forEach(a => {
          if (sectionArticles.some(art => art.url === a.href)) return;

          const container = a.closest('.card, .media-body, li') || a.parentElement;
          const titleEl = container?.querySelector('.article-title, .card-title, h3, h5');
          const oaEl = container?.querySelector('.icon-access-full.text-access-free');

          sectionArticles.push({
            title: titleEl?.textContent.trim() || 'Unknown Title',
            url: a.href,
            section: sectionName,
            isOA: !!oaEl
          });
        });
        currentEl = currentEl.nextElementSibling;
      }

      const filtered = sectionArticles.filter(art => 
        art.section.toLowerCase().includes('research article') || art.section.toLowerCase() === 'articles'
      );
      articles = articles.concat(filtered.map(({ section, ...rest }) => rest));
    });

    console.log(`${LOG_PREFIX} Total articles: ${articles.length}`);
    const nextIssueUrl = findNextIssueUrlScience();
    sendDataToBackground({ articles, journal, publicationDate, nextIssueUrl });
  }

  function scrapeCellPage() {
    console.log(`${LOG_PREFIX} Scraping Cell...`);
    
    const journal = extractCellJournalName();
    const issueDateEl = document.querySelector('.toc-header__issue-date, .issue-header__date');
    const publicationDate = issueDateEl?.textContent.trim() || 'Unknown Date';
    
    console.log(`${LOG_PREFIX} Journal: ${journal}, Publication Date: ${publicationDate}`);

    const sections = Array.from(document.querySelectorAll('.toc__section'));
    let articles = [];

    sections.forEach((section) => {
      const header = section.querySelector('h2.toc__heading__header, h2.toc__heading');
      if (!header) return;
      const sectionName = header.textContent.trim();
      
      // Skip "Reviews" or "Review Articles" sections
      if (sectionName.toLowerCase().includes('review')) {
        console.log(`${LOG_PREFIX} Skipping review section: ${sectionName}`);
        return;
      }

      if (sectionName !== 'Articles') return;
      const sectionArticles = links
        .filter(a => a.href.includes('/fulltext/') && a.textContent.includes('Full-Text HTML'))
        .map(a => {
          const li = a.closest('li.articleCitation, li');
          const titleEl = li?.querySelector('.toc__item__title');
          const oaEl = li?.querySelector('.OALabel');

          return {
            title: titleEl?.textContent.trim() || 'Unknown Title',
            url: a.href,
            isOA: !!oaEl
          };
        });
      
      articles = articles.concat(sectionArticles);
    });

    console.log(`${LOG_PREFIX} Total articles: ${articles.length}`);
    const nextIssueUrl = findNextIssueUrlCell();
    sendDataToBackground({ articles, journal, publicationDate, nextIssueUrl });
  }

  // --- Helpers ---

  function extractNatureJournalCode() {
    try {
      const dataLayerScript = document.querySelector('script[data-test="dataLayer"]');
      if (dataLayerScript) {
        const match = dataLayerScript.textContent.match(/"pcode":"([^"]+)"/);
        if (match) return match[1];
      }
    } catch (e) {
      console.error(`${LOG_PREFIX} Error extracting journal code:`, e);
    }
    return 'nature';
  }

  function extractNatureJournalName() {
    try {
      const dataLayerScript = document.querySelector('script[data-test="dataLayer"]');
      if (dataLayerScript) {
        const match = dataLayerScript.textContent.match(/"title":"([^"]+)"/);
        if (match) return match[1];
      }
    } catch (e) {
      console.error(`${LOG_PREFIX} Error extracting journal name:`, e);
    }
    return 'Nature';
  }

  function extractScienceJournalName() {
    try {
      const script = Array.from(document.querySelectorAll('script')).find(s => s.textContent.includes('AAASdataLayer'));
      if (script) {
        const match = script.textContent.match(/"pageTitle":"Contents \| ([^0-9,]+)/);
        if (match) return match[1].trim();
      }
    } catch (e) {
      console.error(`${LOG_PREFIX} Error extracting Science journal name:`, e);
    }
    const pathParts = window.location.pathname.split('/').filter(Boolean);
    return (pathParts[0] === 'toc' && pathParts.length >= 2) ? pathParts[1] : 'Science';
  }

  function extractCellJournalName() {
    let name = 'Cell';
    try {
      const logoLink = document.querySelector('a#cpIconLnk');
      if (logoLink && logoLink.title) {
        name = logoLink.title;
      } else {
        const meta = document.querySelector('meta[name="pbContext"]');
        if (meta) {
          const match = meta.content.match(/journal:journal:([^;]+)/);
          if (match) {
            const code = match[1];
            name = code.charAt(0).toUpperCase() + code.slice(1);
          }
        } else {
          name = window.location.pathname.split('/').filter(Boolean)[0] || 'Cell';
        }
      }
    } catch (e) {
      console.error(`${LOG_PREFIX} Error extracting Cell journal name:`, e);
      name = window.location.pathname.split('/').filter(Boolean)[0] || 'Cell';
    }

    // Ensure it starts with "Cell"
    if (!name.startsWith('Cell')) {
      name = `Cell ${name}`;
    }
    return name;
  }

  function extractNaturePagination() {
    const resultsDataEl = document.querySelector('[data-test="results-data"]');
    let totalResults = 0, pageSize = 50;
    if (resultsDataEl) {
      const text = resultsDataEl.textContent;
      const totalMatch = text.match(/of\s+([\d,]+)/) || text.match(/([\d,]+)\s+results/);
      if (totalMatch) totalResults = parseInt(totalMatch[1].replace(/,/g, ''), 10);
      const rangeMatch = text.match(/(\d+)–(\d+)/);
      if (rangeMatch) pageSize = parseInt(rangeMatch[2], 10) - parseInt(rangeMatch[1], 10) + 1;
    }
    return { totalResults, pageSize };
  }

  function findNextIssueUrlNature() {
    const btn = document.querySelector('a[data-track-label="next issue"], a.c-issue-navigation__link--next, a[data-track-action="next link"]');
    return btn?.href || null;
  }

  function findNextIssueUrlScience() {
    const btn = document.querySelector('a.content-navigation__btn--next, a[title="Next"]');
    return (btn?.href && !btn.classList.contains('disabled') && btn.getAttribute('href') !== '#') ? btn.href : null;
  }

  function findNextIssueUrlCell() {
    const btn = document.querySelector('a.content-navigation__btn--next, .issue-navigation__next, a[title="Next Issue"]');
    return (btn?.href && !btn.classList.contains('disabled') && btn.getAttribute('href') !== '#') ? btn.href : null;
  }

  function sendHeartbeat(journal, publicationDate) {
    chrome.runtime.sendMessage({
      action: 'pageScraped',
      data: {
        articles: [],
        journal,
        publicationDate,
        htmlContent: document.documentElement.outerHTML,
        currentUrl: window.location.href,
        isHeartbeat: true
      }
    });
  }

  function sendDataToBackground(payload) {
    chrome.runtime.sendMessage({
      action: 'pageScraped',
      data: {
        htmlContent: document.documentElement.outerHTML,
        currentUrl: window.location.href,
        ...payload
      }
    });
  }
})();
