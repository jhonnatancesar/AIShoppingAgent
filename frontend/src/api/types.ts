export type UserRole = 'USER' | 'ADMIN' | 'DEV'

export interface WebSessionUser {
  id: string
  display_name: string
  username: string | null
  role: UserRole
}
