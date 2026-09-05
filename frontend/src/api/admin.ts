import { api } from './client'
export type Lifecycle='active'|'inactive'|'blocked'|'deleted'
export interface Worker { service:'collection_worker'|'telegram_notifier'; status:string; detail?:string|null }
export interface Store { id:string; code:string; name:string; is_active:boolean; last_run_status?:string|null; last_run_at?:string|null }
export interface Dashboard { generated_at:string; api:string; postgresql:string; redis:string; users:{total:number;active:number}; missions:{total:number;active:number}; collections:{total:number;failed_24h:number}; events:{total:number;failed_24h:number}; stores:Store[]; workers:Worker[]; ai_history_available:boolean; circuit_state_available:boolean }
export interface AdminUser { id:string; display_name:string; username?:string|null; email?:string|null; role:'USER'|'DEV'|'ADMIN'; lifecycle_status:Lifecycle; is_active:boolean; mission_count:number; max_active_missions_override?:number|null; max_store_slots_override?:number|null; max_daily_searches_override?:number|null }
export interface AdminMission { id:string; title:string; status:string; state_version:number }

// TASK-108: fila justa por usuário + pacing global por loja -- somente
// leitura nesta Subtask (16), a edição de `PATCH /admin/queue/config`
// fica fora de escopo por decisão explícita.
export interface QueueConfig {
  max_concurrent_user_batches:number; max_concurrent_user_batches_override:number|null
  user_cooldown_min_seconds:number; user_cooldown_min_seconds_override:number|null
  user_cooldown_max_seconds:number; user_cooldown_max_seconds_override:number|null
  store_min_interval_seconds:number; store_min_interval_seconds_override:number|null
}
export interface QueueUserState { user_id:string; display_name:string; is_processing_now:boolean; queue_position:number|null; last_processed_at:string|null; next_eligible_at:string|null; cooldown_active:boolean }
export interface QueueStoreThrottle { store_id:string; code:string; name:string; next_allowed_at:string|null; throttled:boolean }
export interface QueueDashboard { generated_at:string; config:QueueConfig; users:QueueUserState[]; stores:QueueStoreThrottle[] }

export interface ApiKeysStatus { enabled:boolean; authentication_enabled:boolean; issuance_enabled:boolean; message:string }

// Subtask 7 (backend) / Subtask 16 (primeira UI real).
export type FeedbackKind = 'bug'|'support'|'store_suggestion'
export type FeedbackChannel = 'web'|'telegram'
export type FeedbackStatus = 'new'|'reviewed'|'closed'
export interface FeedbackItem {
  id:string; user_id:string|null; user_display_name:string|null
  kind:FeedbackKind; channel:FeedbackChannel; message:string|null
  store_name:string|null; store_url:string|null
  status:FeedbackStatus; created_at:string
}
export interface FeedbackListResponse { items:FeedbackItem[]; limit:number; offset:number; total:number }

export const adminApi={
 dashboard:()=>api.get<Dashboard>('/admin/dashboard'), users:(q='')=>api.get<{items:AdminUser[]}>(`/admin/users${q?`?q=${encodeURIComponent(q)}`:''}`),
 createUser:(body:unknown)=>api.post<AdminUser>('/admin/users',body), updateUser:(id:string,body:unknown)=>api.patch<AdminUser>(`/admin/users/${id}`,body), deleteUser:(id:string,body:unknown)=>api.del<void>(`/admin/users/${id}`,body),
 missions:(id:string)=>api.get<AdminMission[]>(`/admin/users/${id}/missions`), missionCommand:(id:string,body:unknown)=>api.post<AdminMission>(`/admin/missions/${id}/command`,body),
 serviceAction:(body:unknown)=>api.post<Worker>('/admin/services/action',body), provider:(id:string,body:unknown)=>api.patch<Store>(`/admin/providers/${id}`,body), trigger:(body:unknown)=>api.post<{status:string}>('/admin/collections/trigger',body),
 queue:()=>api.get<QueueDashboard>('/admin/queue'),
 apiKeysStatus:()=>api.get<ApiKeysStatus>('/admin/api-keys/status'),
 feedback:(params:{status?:FeedbackStatus;kind?:FeedbackKind;limit?:number;offset?:number}={})=>{
  const search=new URLSearchParams()
  if(params.status) search.set('status',params.status)
  if(params.kind) search.set('kind',params.kind)
  search.set('limit',String(params.limit??20))
  search.set('offset',String(params.offset??0))
  return api.get<FeedbackListResponse>(`/admin/feedback?${search.toString()}`)
 },
 updateFeedbackStatus:(id:string,status:FeedbackStatus)=>api.patch<FeedbackItem>(`/admin/feedback/${id}`,{status}),
}
