import React, { useState } from 'react';
import './App.css';

function AnalyzerForm({ onSearch, disabled }) {
  const [username, setUsername] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (username.trim()) {
      onSearch(username.trim().toLowerCase());
      setUsername('');
    }
  };

  return (
    <form className="search-form" onSubmit={handleSubmit}>
      <input
        className="search-input"
        type="text"
        placeholder="Enter opponent's Chess.com username..."
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        disabled={disabled}
      />
      <button className="search-button" type="submit" disabled={disabled}>
        Analyze
      </button>
    </form>
  );
}

function ResultsDisplay({ results, showECO, aiInsights, loadingAI, onGetInsights }) {
  const openings = results?.openings || [];
  const totalGames = results?.total_games_analyzed || 0;

  return (
    <div className="results-container">
      <div className="results-header">
        <h2>{results.username}</h2>
        <p style={{ color: 'var(--color-text-muted)', fontSize: '0.95rem' }}>
          Analysis based on {totalGames} rated games
        </p>
      </div>

      <div className="results-stats">
        <div className="stat-item">
          <span className="stat-label">Total Games</span>
          <span className="stat-value">{totalGames}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Openings Played</span>
          <span className="stat-value">{openings.length}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Most Played Opening</span>
          <span className="stat-value" style={{ fontSize: '1.1rem' }}>
            {openings[0]?.name?.split(':')[0] || 'N/A'}
          </span>
        </div>
      </div>

      <div>
        <h3 style={{ marginBottom: '1rem', color: 'var(--color-text)' }}>Opening Repertoire</h3>
        <div className="openings-list">
          <div className="opening-card" style={{ background: 'var(--color-green-light)', fontWeight: 600 }}>
            <div>Opening</div>
            <div>Frequency</div>
            <div>Games</div>
            <div>Win Rate</div>
          </div>
          {openings.map((opening, idx) => (
            <div key={idx} className="opening-card">
              <div className="opening-name">
                {opening.name}
                {showECO && opening.eco && (
                  <div className="opening-eco">[{opening.eco}]</div>
                )}
              </div>
              <div className="stat-value-small">{opening.frequency.toFixed(1)}%</div>
              <div className="stat-value-small">{opening.games}</div>
              <div className="stat-value-small win-rate">{opening.win_rate.toFixed(1)}%</div>
            </div>
          ))}
        </div>
      </div>

      <div className="ai-insights-section">
        <div className="ai-insights-header">
          <h3>AI Insights</h3>
          <button
            className="get-insights-button"
            onClick={onGetInsights}
            disabled={loadingAI || aiInsights !== null}
          >
            {loadingAI ? 'Analyzing...' : aiInsights ? 'Insights Loaded' : 'Get Insights'}
          </button>
        </div>

        {aiInsights && (
          <div className="ai-insights-list">
            {aiInsights.map((insight, idx) => (
              <div key={idx} className="ai-insight-item">
                {insight}
              </div>
            ))}
          </div>
        )}

        {!aiInsights && !loadingAI && (
          <p style={{ color: 'var(--color-text-muted)', fontSize: '0.95rem' }}>
            Click "Get Insights" to receive AI-powered analysis of this opponent's strengths and weaknesses.
          </p>
        )}
      </div>
    </div>
  );
}

function LoadingSpinner() {
  return (
    <div className="loading-container">
      <div className="spinner"></div>
      <span className="loading-text">Fetching games and analyzing openings...</span>
    </div>
  );
}

function App() {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showECO, setShowECO] = useState(false);
  const [aiInsights, setAiInsights] = useState(null);
  const [loadingAI, setLoadingAI] = useState(false);

  const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:8000';

  const handleSearch = async (username) => {
    setLoading(true);
    setError(null);
    setResults(null);
    setAiInsights(null);

    try {
      const response = await fetch(`${API_BASE}/api/analyze/${username}`);
      
      if (!response.ok) {
        throw new Error(`Player not found or error: ${response.statusText}`);
      }

      const data = await response.json();
      setResults(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGetAIInsights = async () => {
    if (!results) return;

    setLoadingAI(true);

    try {
      const response = await fetch(
        `${API_BASE}/api/analyze/${results.username}?include_ai=true`
      );

      if (!response.ok) {
        throw new Error('Failed to get AI insights');
      }

      const data = await response.json();
      setAiInsights(data.ai_insights);
    } catch (err) {
      setError('Could not fetch AI insights: ' + err.message);
    } finally {
      setLoadingAI(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>Chess Opening Analyzer</h1>
        <p>Discover opponent weaknesses through opening analysis</p>
      </header>

      <main className="app-main">
        <AnalyzerForm onSearch={handleSearch} disabled={loading} />

        {error && <div className="error-message">{error}</div>}

        {loading && <LoadingSpinner />}

        {results && (
          <>
            <div className="controls">
              <label className="eco-toggle">
                <input
                  type="checkbox"
                  checked={showECO}
                  onChange={(e) => setShowECO(e.target.checked)}
                />
                Show ECO Codes
              </label>
            </div>

            <ResultsDisplay
              results={results}
              showECO={showECO}
              aiInsights={aiInsights}
              loadingAI={loadingAI}
              onGetInsights={handleGetAIInsights}
            />
          </>
        )}
      </main>
    </div>
  );
}

export default App;
