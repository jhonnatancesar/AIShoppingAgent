import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  const saved = localStorage.getItem('aishopping-theme')
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.style.colorScheme = theme
    localStorage.setItem('aishopping-theme', theme)
  }, [theme])

  const label = theme === 'dark' ? 'Usar tema claro' : 'Usar tema escuro'

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" type="button" onClick={() => setTheme((value) => value === 'dark' ? 'light' : 'dark')} aria-label={label}>
          {theme === 'dark' ? <Sun /> : <Moon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}
