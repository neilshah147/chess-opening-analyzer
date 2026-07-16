from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import chess
import json
import os
from typing import Optional
import csv
from io import StringIO

# Groq configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

app = FastAPI()

# CORS middleware for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load Lichess openings dataset
OPENINGS_DB = {}

def load_openings_db():
    """Load Lichess openings database from TSV format"""
    # We'll fetch from GitHub or use inline data
    # For now, using a simplified version - can be expanded
    openings_csv = """move_sequence	eco	name
e2e4	B20	Sicilian Defense
e2e4 c7c5	B20	Sicilian Defense: Open
e2e4 c7c5 g1f3	B21	Sicilian Defense: Closed
e2e4 e7e5	C20	Open Game
e2e4 e7e5 g1f3	C25	Vienna Game
d2d4	D00	Queen's Gambit Declined
d2d4 d7d5	D05	Queen's Gambit Declined
d2d4 g8f6	D10	Slav Defense
c2c4	A10	English Opening
g1f3	A04	Reti Opening"""
    
    lines = openings_csv.strip().split('\n')
    reader = csv.DictReader(StringIO('\n'.join(lines)), delimiter='\t')
    
    for row in reader:
        OPENINGS_DB[row['move_sequence']] = {
            'eco': row['eco'],
            'name': row['name']
        }

load_openings_db()

class OpeningClassifier:
    """Classify chess openings from PGN"""
    
    @staticmethod
    def get_opening_moves(pgn_string: str, max_moves: int = 10) -> Optional[dict]:
        """Extract opening from PGN and classify it"""
        try:
            board = chess.Board()
            move_sequence = ""
            move_count = 0
            
            # Parse PGN moves
            pgn_tokens = pgn_string.split()
            
            for token in pgn_tokens:
                # Skip move numbers and annotations
                if token[-1] in '.!?':
                    token = token[:-1]
                if token[-1] in '!?':
                    token = token[:-1]
                
                if any(c.isdigit() for c in token) and token[0].isdigit():
                    continue
                
                try:
                    move = board.push_san(token)
                    move_sequence += move.uci() + " "
                    move_count += 1
                    
                    if move_count >= max_moves:
                        break
                except:
                    pass
            
            move_sequence = move_sequence.strip()
            
            # Try exact match and partial matches
            for i in range(len(move_sequence.split()), 0, -1):
                key = " ".join(move_sequence.split()[:i])
                if key in OPENINGS_DB:
                    return {
                        'moves': key,
                        'eco': OPENINGS_DB[key]['eco'],
                        'name': OPENINGS_DB[key]['name']
                    }
            
            # Fallback: return first move
            first_move = move_sequence.split()[0] if move_sequence else None
            return {
                'moves': move_sequence,
                'eco': None,
                'name': 'Unknown Opening'
            }
        except Exception as e:
            print(f"Error classifying opening: {e}")
            return None

async def fetch_player_games(username: str, limit: int = 500) -> list:
    """Fetch player's games from Chess.com API"""
    games = []
    
    try:
        async with httpx.AsyncClient() as client:
            # Get archives list
            archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
            archives_response = await client.get(archives_url, timeout=10)
            archives_response.raise_for_status()
            archives_data = archives_response.json()
            
            archives = archives_data.get('archives', [])
            
            # Iterate through recent months
            for archive_url in reversed(archives[-12:]):  # Last 12 months
                archive_response = await client.get(archive_url, timeout=10)
                archive_response.raise_for_status()
                archive_data = archive_response.json()
                
                for game in archive_data.get('games', []):
                    # Only include rated games
                    if game.get('rated'):
                        games.append(game)
                        if len(games) >= limit:
                            return games
    
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch player games: {str(e)}")
    
    return games

def analyze_openings(games: list) -> dict:
    """Analyze opening statistics from games"""
    opening_stats = {}
    
    for game in games:
        pgn = game.get('pgn', '')
        
        opening_info = OpeningClassifier.get_opening_moves(pgn)
        if not opening_info:
            continue
        
        opening_name = opening_info['name']
        eco = opening_info.get('eco')
        
        if opening_name not in opening_stats:
            opening_stats[opening_name] = {
                'name': opening_name,
                'eco': eco,
                'games': 0,
                'wins': 0,
                'losses': 0,
                'draws': 0,
                'frequency': 0
            }
        
        # Determine if player was white or black
        white_username = game.get('white', {}).get('username', '').lower() if isinstance(game.get('white'), dict) else ''
        black_username = game.get('black', {}).get('username', '').lower() if isinstance(game.get('black'), dict) else ''
        
        player_is_white = white_username == username.lower() if 'username' in locals() else False
        
        result_key = 'white' if player_is_white else 'black'
        result = game.get(result_key, {}).get('result') if isinstance(game.get(result_key), dict) else ''
        
        opening_stats[opening_name]['games'] += 1
        
        if result == 'win':
            opening_stats[opening_name]['wins'] += 1
        elif result == 'loss':
            opening_stats[opening_name]['losses'] += 1
        elif result in ['draw', 'agreed']:
            opening_stats[opening_name]['draws'] += 1
    
    # Calculate frequencies and sort
    total_games = sum(s['games'] for s in opening_stats.values())
    for stats in opening_stats.values():
        stats['frequency'] = (stats['games'] / total_games * 100) if total_games > 0 else 0
        stats['win_rate'] = (stats['wins'] / stats['games'] * 100) if stats['games'] > 0 else 0
    
    # Sort by frequency
    sorted_openings = sorted(
        opening_stats.values(),
        key=lambda x: x['frequency'],
        reverse=True
    )
    
    return {'openings': sorted_openings, 'total_games': total_games}

async def get_ai_insights(opponent_data: dict) -> list:
    """Get AI insights using Groq"""
    if not GROQ_API_KEY:
        return ["Groq API key not configured"]
    
    try:
        openings_summary = "\n".join([
            f"- {o['name']} ({o['eco'] or 'Unknown ECO'}): {o['games']} games, {o['win_rate']:.1f}% win rate"
            for o in opponent_data['openings'][:10]
        ])
        
        prompt = f"""Analyze this chess player's opening repertoire and provide 3-4 bullet-point insights about their strengths and weaknesses:

{openings_summary}

Total games analyzed: {opponent_data['total_games']}

Provide concise, actionable insights for someone preparing to play against this opponent. Focus on:
1. Their strongest openings
2. Their weaker systems
3. Recommended preparation strategy

Keep each point to one sentence."""
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "mixtral-8x7b-32768",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.7
                },
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"Groq API error: {response.status_code} - {response.text}")
                return ["Could not generate AI insights at this time"]
            
            data = response.json()
            text = data['choices'][0]['message']['content']
            
            # Parse response into bullet points
            insights = text.split('\n')
            insights = [line.strip() for line in insights if line.strip() and (line.strip()[0] == '-' or line.strip()[0].isdigit())]
            
            return insights[:4] if insights else ["Analysis complete - see opening statistics above"]
    except Exception as e:
        print(f"Error getting AI insights: {e}")
        return ["Could not generate AI insights at this time"]

@app.get("/api/analyze/{username}")
async def analyze_opponent(username: str, include_ai: Optional[bool] = False):
    """Analyze opponent's opening repertoire"""
    try:
        # Fetch games
        games = await fetch_player_games(username)
        
        if not games:
            raise HTTPException(status_code=404, detail=f"No games found for player {username}")
        
        # Analyze openings
        opening_analysis = analyze_openings(games)
        
        # Get AI insights if requested
        ai_insights = []
        if include_ai:
            ai_insights = await get_ai_insights(opening_analysis)
        
        return {
            'username': username,
            'total_games_analyzed': opening_analysis['total_games'],
            'openings': opening_analysis['openings'],
            'ai_insights': ai_insights
        }
    
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing opponent: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
