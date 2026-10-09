"""Bounded semantic retrieval from administrator-configured policy vector stores."""
from pydantic import BaseModel, Field


class PolicySearchHit(BaseModel):
    file_id: str
    filename: str
    score: float
    text: str


class PolicySearchResult(BaseModel):
    matches: list[PolicySearchHit] = Field(max_length=10)
    advisory_only: bool = True


def search_policies(container, query):
    settings = container.settings
    if not settings.enterprise_ai_file_search_enabled:
        raise NotImplementedError('Policy vector search is not configured')
    matches = []
    for store in settings.enterprise_ai_vector_store_ids:
        result = container.responses.transport.request('POST', '/vector_stores/' + store + '/search',
            {'query': query, 'max_num_results': 5})
        for row in result.get('data', []):
            matches.append(PolicySearchHit(file_id=row['file_id'], filename=row['filename'], score=row['score'],
                text='\n'.join(part['text'] for part in row.get('content', [])
                               if part.get('type') == 'text')[:12000]))
    return PolicySearchResult(matches=sorted(matches, key=lambda h: h.score, reverse=True)[:10])
