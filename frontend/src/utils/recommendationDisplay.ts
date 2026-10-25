import type { AIChatResponse, RecommendationDisplay } from '@/types/aiChat'

const recommendationGoals = new Set([
  'public_travel_advice', 'health_protection', 'governance_advice'
])

export function recommendationDisplayFor(response?: AIChatResponse): RecommendationDisplay | null {
  if (!response?.user_goal || !recommendationGoals.has(response.user_goal)) return null
  const structured = response.recommendation_display
  if (!structured || structured.recommendation_type !== response.user_goal) return null
  if (response.recommendation_result
    && response.recommendation_result.recommendation_type !== response.user_goal) return null
  return structured.explanation?.trim() ? structured : null
}
