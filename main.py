from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import chess
import json
import os
from typing import Optional
import asyncio

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENINGS_DB = {}

async def load_openings_db():
    """Load Lichess openings database from API"""
    global OPENINGS_DB
    try:
        async with httpx.AsyncClient() as client:
            print("Fetching Lichess openings database...")
            response = await client.get("https://lichess.org/api/opening/tree/main", timeout=20)
            response.raise_for_status()
            data = response.json()
            
            # Build opening database from tree
            def process_openings(node, prefix=""):
                if 'uci' in node:
                    moves_str = prefix + (" " + node['uci'] if prefix else node['uci'])
                else:
                    moves_str = prefix
                
                if moves_str and 'eco' in node and 'name' in node:
                    OPENINGS_DB[moves_str] = {
                        'eco': node.get('eco'),
                        'name': node.get('name')
                    }
                
                # Recursively process children
                for child in node.get('children', []):
                    new_prefix = moves_str + (" " + child.get('uci', '')) if moves_str else child.get('uci', '')
                    process_openings(child, new_prefix)
            
            process_openings(data)
            print(f"Loaded {len(OPENINGS_DB)} openings from Lichess")
    except Exception as e:
        print(f"Warning: Could not load Lichess openings: {e}")

# Load openings on startup
try:
    asyncio.run(load_openings_db())
except:
    print("Could not load openings asynchronously, will load on first request")

class OpeningClassifier:
    @staticmethod
    def get_opening_moves(pgn_string: str, max_moves: int = 10) -> Optional[dict]:
        try:
            board = chess.Board()
            move_sequence = ""
            move_count = 0
            pgn_tokens = pgn_string.split()
            
            for token in pgn_tokens:
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
            
            # Try to match against database
            for i in range(len(move_sequence.split()), 0, -1):
                key = " ".join(move_sequence.split()[:i])
                if key in OPENINGS_DB:
                    return {
                        'moves': key,
                        'eco': OPENINGS_DB[key]['eco'],
                        'name': OPENINGS_DB[key]['name']
                    }
            
            return {
                'moves': move_sequence,
                'eco': None,
                'name': 'Unknown Opening'
            }
        except Exception as e:
            print(f"Error classifying opening: {e}")
            return None

async def fetch_player_games(username: str, limit: int = 500) -> list:
    games = []
    try:
        async with httpx.AsyncClient() as client:
            archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
            archives_response = await client.get(archives_url, timeout=10)
            archives_response.raise_for_status()
            archives_data = archives_response.json()
            archives = archives_data.get('archives', [])
            
            for archive_url in reversed(archives[-12:]):
                archive_response = await client.get(archive_url, timeout=10)
                archive_response.raise_for_status()
                archive_data = archive_response.json()
                
                for game in archive_data.get('games', []):
                    if game.get('rated'):
                        games.append(game)
                        if len(games) >= limit:
                            return games
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch player games: {str(e)}")
    
    return games

def analyze_openings(games: list, username: str) -> dict:
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
        
        # Determine player color and result
        white_player = game.get('white', {})
        black_player = game.get('black', {})
        
        white_username = white_player.get('username', '').lower() if isinstance(white_player, dict) else ''
        black_username = black_player.get('username', '').lower() if isinstance(black_player, dict) else ''
        player_lower = username.lower()
        
        if white_username == player_lower:
            result = white_player.get('result', '') if isinstance(white_player, dict) else ''
        elif black_username == player_lower:
            result = black_player.get('result', '') if isinstance(black_player, dict) else ''
        else:
            continue
        
        opening_stats[opening_name]['games'] += 1
        
        if result == 'win':
            opening_stats[opening_name]['wins'] += 1
        elif result == 'loss':
            opening_stats[opening_name]['losses'] += 1
        elif result in ['draw', 'agreed']:
            opening_stats[opening_name]['draws'] += 1
    
    total_games = sum(s['games'] for s in opening_stats.values())
    
    for stats in opening_stats.values():
        stats['frequency'] = (stats['games'] / total_games * 100) if total_games > 0 else 0
        stats['win_rate'] = (stats['wins'] / stats['games'] * 100) if stats['games'] > 0 else 0
    
    sorted_openings = sorted(
        opening_stats.values(),
        key=lambda x: x['frequency'],
        reverse=True
    )
    
    return {'openings': sorted_openings, 'total_games': total_games}

async def get_ai_insights(opponent_data: dict) -> list:
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
                    "model": "openai/gpt-oss-120b",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.7
                },
                timeout=120
            )
            
            if response.status_code != 200:
                print(f"Groq API error: {response.status_code} - {response.text}")
                return ["Could not generate AI insights at this time"]
            
            data = response.json()
            text = data['choices'][0]['message']['content']
            
            insights = text.split('\n')
            insights = [line.strip() for line in insights if line.strip() and (line.strip()[0] == '-' or line.strip()[0].isdigit())]
            
            return insights[:4] if insights else ["Analysis complete - see opening statistics above"]
    except Exception as e:
        print(f"Error getting AI insights: {e}")
        return ["Could not generate AI insights at this time"]

@app.get("/api/analyze/{username}")
async def analyze_opponent(username: str, include_ai: Optional[bool] = False):
    try:
        games = await fetch_player_games(username)
        
        if not games:
            raise HTTPException(status_code=404, detail=f"No games found for player {username}")
        
        opening_analysis = analyze_openings(games, username)
        
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
