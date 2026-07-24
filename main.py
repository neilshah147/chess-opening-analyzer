from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import chess
import os
from typing import Optional
from openings import OPENINGS_EPD

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

LOSS_CODES = {'checkmated', 'resigned', 'timeout', 'abandoned', 'lose'}
DRAW_CODES = {'draw', 'agreed', 'stalemate', 'repetition',
              'insufficient', 'timevsinsufficient', '50move'}


class OpeningClassifier:
    @staticmethod
    def get_opening_from_pgn(pgn_string: str, max_plies: int = 40) -> Optional[dict]:
        """Classify the opening by POSITION, not move order.

        Replays the game and checks each position against the openings
        database (keyed by EPD). The deepest book position reached wins,
        so transpositions classify correctly regardless of move order.
        """
        try:
            board = chess.Board()
            best = None
            plies = 0

            for token in pgn_string.split():
                if token and token[-1] in '.!?':
                    token = token[:-1]
                if token and token[-1] in '!?':
                    token = token[:-1]
                if not token:
                    continue
                if token[-1:] == '.' or token.isdigit():
                    continue
                if token.startswith('{') or token.startswith('[%'):
                    continue
                try:
                    board.push_san(token)
                except Exception:
                    continue
                plies += 1
                hit = OPENINGS_EPD.get(board.epd())
                if hit:
                    best = hit
                if plies >= max_plies:
                    break

            if best:
                return {'name': best['name'], 'eco': best['eco']}
            return {'name': 'Unknown Opening', 'eco': ''}
        except Exception as e:
            print(f"Error classifying opening: {e}")
            return {'name': 'Unknown Opening', 'eco': ''}


async def fetch_player_games(username: str, limit: int = 500, months: int = 12) -> list:
    """Fetch player's rated games from Chess.com.

    months: how many of the player's most recent monthly archives to scan.
            0 (or negative) = all-time, every archive.
    """
    games = []
    try:
        async with httpx.AsyncClient() as client:
            archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
            archives_response = await client.get(archives_url, timeout=10)
            archives_response.raise_for_status()
            archives_data = archives_response.json()
            archives = archives_data.get('archives', [])

            selected = archives if months <= 0 else archives[-months:]

            for archive_url in reversed(selected):
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


def _empty_stats():
    return {'games': 0, 'wins': 0, 'losses': 0, 'draws': 0, 'win_rate': 0}


def _bump(stats, result):
    stats['games'] += 1
    if result == 'win':
        stats['wins'] += 1
    elif result in LOSS_CODES:
        stats['losses'] += 1
    elif result in DRAW_CODES:
        stats['draws'] += 1


def _finalize(stats):
    g = stats['games']
    stats['win_rate'] = (stats['wins'] / g * 100) if g > 0 else 0


def family_of(name: str) -> str:
    """'Caro-Kann Defense: Advance Variation' -> 'Caro-Kann Defense'"""
    return name.split(':')[0].strip()


def analyze_openings(games: list, username: str) -> dict:
    """Analyze opening statistics, split by color, grouped into families."""
    openings = {}

    for game in games:
        pgn = game.get('pgn', '')
        info = OpeningClassifier.get_opening_from_pgn(pgn)
        name = info.get('name', 'Unknown Opening')
        eco = info.get('eco', '')

        white_player = game.get('white', {})
        black_player = game.get('black', {})
        wu = white_player.get('username', '').lower() if isinstance(white_player, dict) else ''
        bu = black_player.get('username', '').lower() if isinstance(black_player, dict) else ''
        pl = username.lower()

        result, color = None, None
        if wu == pl:
            result = white_player.get('result', '') if isinstance(white_player, dict) else ''
            color = 'white'
        elif bu == pl:
            result = black_player.get('result', '') if isinstance(black_player, dict) else ''
            color = 'black'

        if not result:
            continue  # player not in this game - skip before creating entry

        if name not in openings:
            openings[name] = {
                'name': name,
                'eco': eco,
                'family': family_of(name),
                **_empty_stats(),
                'frequency': 0,
                'white': _empty_stats(),
                'black': _empty_stats(),
            }

        _bump(openings[name], result)
        _bump(openings[name][color], result)

    total = sum(o['games'] for o in openings.values())

    for o in openings.values():
        o['frequency'] = (o['games'] / total * 100) if total > 0 else 0
        _finalize(o)
        _finalize(o['white'])
        _finalize(o['black'])

    flat = sorted(openings.values(), key=lambda x: x['frequency'], reverse=True)

    # Aggregate variations into opening families
    families = {}
    for o in flat:
        fam = o['family']
        if fam not in families:
            families[fam] = {
                'name': fam,
                'eco': o['eco'],  # ECO of the most-played variation in the family
                **_empty_stats(),
                'frequency': 0,
                'white': _empty_stats(),
                'black': _empty_stats(),
                'variations': [],
            }
        f = families[fam]
        for side in (None, 'white', 'black'):
            src = o if side is None else o[side]
            dst = f if side is None else f[side]
            dst['games'] += src['games']
            dst['wins'] += src['wins']
            dst['losses'] += src['losses']
            dst['draws'] += src['draws']
        f['variations'].append(o)

    for f in families.values():
        f['frequency'] = (f['games'] / total * 100) if total > 0 else 0
        _finalize(f)
        _finalize(f['white'])
        _finalize(f['black'])

    fam_list = sorted(families.values(), key=lambda x: x['frequency'], reverse=True)

    return {'openings': flat, 'families': fam_list, 'total_games': total}


async def get_ai_insights(opponent_data: dict) -> list:
    """Get AI insights from Groq"""
    if not GROQ_API_KEY:
        return ["Groq API key not configured"]

    try:
        lines = []
        for f in opponent_data['families'][:10]:
            w, b = f['white'], f['black']
            lines.append(
                f"- {f['name']}: {f['games']} games total | "
                f"as White: {w['games']} games, {w['win_rate']:.0f}% wins | "
                f"as Black: {b['games']} games, {b['win_rate']:.0f}% wins"
            )
        openings_summary = "\n".join(lines)

        prompt = f"""Analyze this chess player's opening repertoire. Provide 3-4 bullet-point insights:

{openings_summary}

Total games: {opponent_data['total_games']}

Focus on: 1) Strongest openings (note which color), 2) Weaknesses to target, 3) Prep strategy against them. One sentence per point."""

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
async def analyze_opponent(username: str, include_ai: Optional[bool] = False, months: Optional[int] = 12):
    try:
        games = await fetch_player_games(username, months=months if months is not None else 12)

        if not games:
            raise HTTPException(status_code=404, detail=f"No games found for player {username}")

        opening_analysis = analyze_openings(games, username)

        ai_insights = []
        if include_ai:
            ai_insights = await get_ai_insights(opening_analysis)

        return {
            'username': username,
            'total_games_analyzed': opening_analysis['total_games'],
            'months': months,
            'openings': opening_analysis['openings'],
            'families': opening_analysis['families'],
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
