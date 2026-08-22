import type { ServingPlan } from './client'

export function isServingPlanAuthoritative(plan: ServingPlan | null): plan is ServingPlan {
  return Boolean(plan && plan.snapshot_stale === false && plan.refreshing === false)
}

export function servingPlanStatus(
  plan: ServingPlan | null,
  error: string | null,
  loading: boolean
): 'unavailable' | 'discovering' | 'fresh' {
  if (error) return 'unavailable'
  if (loading || !isServingPlanAuthoritative(plan)) return 'discovering'
  return 'fresh'
}
