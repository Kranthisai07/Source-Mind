import { cn } from '@/lib/utils'
import { type ButtonHTMLAttributes, forwardRef } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', loading, className, children, disabled, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          'inline-flex items-center justify-center font-medium rounded-md transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-1 focus-visible:ring-offset-bg disabled:opacity-50 disabled:cursor-not-allowed select-none',
          {
            // Variants
            'bg-accent-blue text-white hover:bg-[#3d7de8] active:scale-[0.98]': variant === 'primary',
            'bg-elevated text-primary border border-border hover:border-border-active hover:bg-[#222230]': variant === 'secondary',
            'text-secondary hover:text-primary hover:bg-elevated': variant === 'ghost',
            'bg-[rgba(239,68,68,0.12)] text-accent-red border border-[rgba(239,68,68,0.2)] hover:bg-[rgba(239,68,68,0.2)]': variant === 'danger',
            'border border-border text-secondary hover:text-primary hover:border-border-active': variant === 'outline',
            // Sizes
            'text-xs px-2.5 py-1.5 gap-1.5': size === 'sm',
            'text-sm px-4 py-2 gap-2': size === 'md',
            'text-sm px-5 py-2.5 gap-2': size === 'lg',
          },
          className
        )}
        {...props}
      >
        {loading && (
          <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        )}
        {children}
      </button>
    )
  }
)
Button.displayName = 'Button'
