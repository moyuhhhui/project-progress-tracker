export type State = 'active' | 'paused' | 'completed' | 'cancelled'
export type Role = 'admin' | 'member' | 'display'
export type Flag = 'overdue' | 'stale' | 'due_soon' | 'blocked'
export interface User { id: string; name: string; role?: Role; active: boolean | number; wecom_user_id?: string }
export interface Actor extends User { role: Role; internal_shared?: boolean }
export interface OwnerAssignment { name: string; role: string; primary: boolean }
export interface Milestone {
  time_text?: string
  id: string; name: string; criterion: string; owner_id?: string; owner_name?: string
  start_date: string; due_date: string; original_due_date?: string; status: State; progress: number
  update_interval?: number; summary: string; blocker: string; next_step: string; expected_date: string | null
  last_report_at: string | null; flags?: Flag[]; pause_reason?: string
}
export interface Project {
  owner_roles?: Record<string, string>
  owner_assignments?: OwnerAssignment[]
  id: string; code: string; version?: number; name: string; description: string
  contact_company?: string; contact_name?: string; contact_info?: string
  owner_id?: string; owner_name?: string; member_ids?: string[]; display_visible?: boolean
  start_date: string; due_date: string; original_due_date?: string; status: State; pause_reason?: string
  progress?: number | null; flags?: Flag[]; risk_score?: number; updated_at: string; milestones: Milestone[]
}
export type Intent = 'record_item' | 'create_project' | 'edit_project' | 'add_milestone' | 'edit_milestone' |
  'report_progress' | 'project_status' | 'milestone_status' | 'create_meeting' | 'query' | 'ignore'
export interface Action { intent: Intent; project_id?: string; milestone_id?: string; data: Record<string, unknown> }
export interface ExecuteActionRequest { action: Action; client_operation_id: string; expected_version?: number }
export interface Meeting { id: string; start_at: string; title: string; project_id?: number | null; attendee_ids: string[]; location: string; notes: string; status: string }
export interface Draft {
  id: string; status: 'pending' | 'needs_input' | 'confirmed' | 'cancelled' | 'expired'
  created_at: string; expires_at: string; expected_version: number | null; action: Action
  due_hour: number
  preview: Project | null; before?: Project | null; source_text: string
  diagnostics: { missing_fields?: string[]; ambiguities?: string[]; evidence?: Record<string, string> } | null
  result?: { project_id: string; code: string; message: string } | null
}
export interface IntegrationStatus {
  ai_configured: boolean; ai_model: string; wecom_inbound: string; wecom_send_enabled: boolean; message: string
}
export interface Snapshot { projects: Project[]; meetings?: Meeting[]; at: string }
export interface History {
  audit: { id: number; actor_name: string; at: string; intent: Intent; before_data: Project | null; after_data: Project }[]
  reports: { id: number; actor_name: string; at: string; milestone_id: string; data: Record<string, unknown> }[]
}
export interface Reminder {
  id: string; project_id: number; milestone_id: string; owner_name: string; reasons: Flag[]
  status: string; attempts: number; updated_at: string; detail: string
}
export interface Settings { start_hour: number; end_hour: number; due_hour: number; workday_overrides: Record<string, boolean> }
export type MessageResult = { kind: 'saved'; result: Record<string, unknown> } |
  { kind: 'batch'; results: Record<string, unknown>[]; failures: { project_name: string; message: string }[]; recognized_actions: number; saved_actions: number; business_failures: number } |
  { kind: 'needs_input'; message: string } | { kind: 'query'; projects: Project[] } |
  { kind: 'ignored'; message: string }
