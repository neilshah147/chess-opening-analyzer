from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import chess
import os
from typing import Optional
from openings import OPENINGS_DB

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

class OpeningClassifier:
    @staticmethod
    def get_opening_from_pgn(pgn_string: str, max_moves: int = 15) -> Optional[dict]:
        """Extract and classify opening from PGN"""
        try:
            board = chess.Board()
            moves_algebraic = ""
            move_count = 0
            move_number = 1
            pgn_tokens = pgn_string.split()
            
            for token in pgn_tokens:
                # Remove annotation symbols
                if token and token[-1] in '.!?':
                    token = token[:-1]
                if token and token[-1] in '!?':
                    token = token[:-1]
                
                # Skip move numbers (they look like "1.", "2.", etc.)
                if token and token[-1] == '.' and token[:-1].isdigit():
                    move_number = int(token[:-1])
                    continue
                
                # Skip Black's move indicators like "1..." "2..." etc.
                if token and token.endswith('...') and token[:-3].isdigit():
                    continue
                
                # Skip if it's a move number without dot
                if token.isdigit():
                    continue
                
                try:
                    move = board.push_san(token)
                    move_count += 1
                    
                    # Build algebraic notation string like "1. e4 c5 2. Nf3"
                    is_white = (move_count % 2 == 1)
                    if is_white:
                        if moves_algebraic:
                            moves_algebraic += f" {move_number}. {token}"
                        else:
                            moves_algebraic = f"1. {token}"
                    else:
                        moves_algebraic += f" {token}"
                        move_number += 1
                    
                    if move_count >= max_moves:
                        break
                except:
                    pass
            
            # Try to match in database (longest match first)
            # Remove trailing move numbers for matching
            clean_moves = moves_algebraic.strip()
            
            for i in range(min(len(clean_moves.split()), 20), 0, -1):
                # Try progressively shorter move sequences
                words = clean_moves.split()
                key = " ".join(words[:i])
                
                if key in OPENINGS_DB:
                    return {
                        'name': OPENINGS_DB[key]['name'],
                        'eco': OPENINGS_DB[key]['eco']
                    }
            
            return {
                'name': 'Unknown Opening',
                'eco': ''
            }
        except Exception as e:
            print(f"Error classifying opening: {e}")
            return {'name': 'Unknown Opening', 'eco': ''}

async def fetch_player_games(username: str, limit: int = 500) -> list:
    """Fetch player's games from Chess.com"""
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
    """Analyze opening statistics"""
    opening_stats = {}
    
    for game in games:
        pgn = game.get('pgn', '')
        opening_info = OpeningClassifier.get_opening_from_pgn(pgn)
        
        opening_name = opening_info.get('name', 'Unknown Opening')
        eco = opening_info.get('eco', '')
        
        if opening_name not in opening_stats:
            opening_stats[opening_name] = {
                'name': opening_name,
                'eco': eco,
                'games': 0,
                'wins': 0,
                'losses': 0,
                'draws': 0,
                'frequency': 0,
                'win_rate': 0
            }
        
        # Get result
        white_player = game.get('white', {})
        black_player = game.get('black', {})
        
        white_username = white_player.get('username', '').lower() if isinstance(white_player, dict) else ''
        black_username = black_player.get('username', '').lower() if isinstance(black_player, dict) else ''
        player_lower = username.lower()
        
        result = None
        if white_username == player_lower:
            result = white_player.get('result', '') if isinstance(white_player, dict) else ''
        elif black_username == player_lower:
            result = black_player.get('result', '') if isinstance(black_player, dict) else ''
        
        if not result:
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
    """Get AI insights from Groq"""
    if not GROQ_API_KEY:
        return ["Groq API key not configured"]
    
    try:
        openings_summary = "\n".join([
            f"- {o['name']} ({o['eco'] or 'Unknown'}): {o['games']} games, {o['win_rate']:.1f}% win rate"
            for o in opponent_data['openings'][:10]
        ])
        
        prompt = f"""Analyze this chess player's opening repertoire. Provide 3-4 bullet-point insights:

{openings_summary}

Total games: {opponent_data['total_games']}

Focus on: 1) Strongest openings, 2) Weaknesses, 3) Prep strategy. One sentence per point."""
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500, "temperature": 0.7},
                timeout=120
            )
            
            if response.status_code != 200:
                return ["Could not generate AI insights"]
            
            data = response.json()
            text = data['choices'][0]['message']['content']
            insights = [line.strip() for line in text.split('\n') if line.strip() and (line.strip()[0] == '-' or line.strip()[0].isdigit())]
            return insights[:4] if insights else ["Analysis complete"]
    except:
        return ["Could not generate AI insights"]

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
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
