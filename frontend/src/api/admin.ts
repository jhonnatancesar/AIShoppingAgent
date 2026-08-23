import { api } from './client'
export type Lifecycle='active'|'inactive'|'blocked'|'deleted'
export interface Worker { service:'collection_worker'|'telegram_notifier'; status:string; detail?:string|null }
export interface Store { id:string; code:string; name:string; is_active:boolean; last_run_status?:string|null; last_run_at?:string|null }
export interface Dashboard { generated_at:string; api:string; postgresql:string; redis:string; users:{total:number;active:number}; missions:{total:number;active:number}; collections:{total:number;failed_24h:number}; events:{total:number;failed_24h:number}; stores:Store[]; workers:Worker[]; ai_history_available:boolean; circuit_state_available:boolean }
export interface AdminUser { id:string; display_name:string; username?:string|null; email?:string|null; role:'USER'|'DEV'|'ADMIN'; lifecycle_status:Lifecycle; is_active:boolean; mission_count:number; max_active_missions_override?:number|null; max_store_slots_override?:number|null; max_daily_searches_override?:number|null }
export interface AdminMission { id:string; title:string; status:string; state_version:number }
export const adminApi={
 dashboard:()=>api.get<Dashboard>('/admin/dashboard'), users:(q='')=>api.get<{items:AdminUser[]}>(`/admin/users${q?`?q=${encodeURIComponent(q)}`:''}`),
 createUser:(body:unknown)=>api.post<AdminUser>('/admin/users',body), updateUser:(id:string,body:unknown)=>api.patch<AdminUser>(`/admin/users/${id}`,body), deleteUser:(id:string,body:unknown)=>api.del<void>(`/admin/users/${id}`,body),
 missions:(id:string)=>api.get<AdminMission[]>(`/admin/users/${id}/missions`), missionCommand:(id:string,body:unknown)=>api.post<AdminMission>(`/admin/missions/${id}/command`,body),
 serviceAction:(body:unknown)=>api.post<Worker>('/admin/services/action',body), provider:(id:string,body:unknown)=>api.patch<Store>(`/admin/providers/${id}`,body), trigger:(body:unknown)=>api.post<{status:string}>('/admin/collections/trigger',body),
}
