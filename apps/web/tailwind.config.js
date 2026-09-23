/** @type {import('tailwindcss').Config} */

/* ---------------------------------------------------------------------------
   LAYER 1 — PRIMITIVE
   The single source of truth for Tailwind utilities. Mirrored as CSS custom
   properties in src/styles/tokens.css for the consumers Tailwind cannot reach
   (component sizing, keyframes, non-utility CSS). Change a value here and in
   tokens.css together - they are two views of one system.

   These stay literal hex rather than var() because Tailwind's opacity
   modifiers (bg-accent/10) cannot decompose a var holding a full colour.
   ------------------------------------------------------------------------ */
const primitive = {
    neutral950: '#0A0A0F',
    neutral900: '#12121A',
    neutral850: '#16161F',
    neutral800: '#1E1E2E',
    neutral700: '#2A2A3E',
    neutral500: '#4A4A6A',
    neutral300: '#8888A8',
    neutral50:  '#E8E8F0',
    blue500:    '#4F7EFF',
    blue600:    '#3D6BEE',
    blue400:    '#7C9DFF',
    green500:   '#34D399',
    red500:     '#EF4444',
    amber500:   '#F59E0B',
};

module.exports = {
    darkMode: ["class"],
    content: [
        "./src/**/*.{js,jsx,ts,tsx}",
        "./public/index.html"
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
                mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
            },
            /* LAYER 3 — COMPONENT. §3's type scale, addressable as text-h1 etc.
               so a component never hardcodes text-[28px]. */
            fontSize: {
                'h1':    ['28px', { lineHeight: '1.2',  fontWeight: '700' }],
                'title': ['15px', { lineHeight: '1.35', fontWeight: '600' }],
                'body':  ['13px', { lineHeight: '1.55' }],
                'body-lg': ['14px', { lineHeight: '1.55' }],
                'micro': ['11px', { lineHeight: '1.3',  letterSpacing: '0.08em' }],
                'hero':  ['34px', { lineHeight: '1.1',  fontWeight: '600' }],
            },
            spacing: {
                'sidebar':   '260px',   /* §1 "~260px" */
                'topstrip':  '48px',    /* §1 "~48px"  */
                'navitem':   '34px',
                'tablerow':  '52px',
            },
            borderRadius: {
                lg: 'var(--radius)',
                md: 'calc(var(--radius) - 2px)',
                sm: 'calc(var(--radius) - 4px)',
                card: '12px',
            },
            colors: {
                /* LAYER 1 exposed for the rare case a primitive is genuinely
                   what is meant. Prefer the semantic names below. */
                sm: {
                    bg: primitive.neutral950,
                    surface: primitive.neutral900,
                    'surface-2': '#17172160',
                    border: primitive.neutral800,
                    'border-hover': primitive.neutral700,
                    text: primitive.neutral50,
                    'text-secondary': primitive.neutral300,
                    'text-muted': primitive.neutral500,
                    blue: primitive.blue500,
                    green: primitive.green500,
                    amber: primitive.amber500,
                    red: primitive.red500,
                    /* DEPRECATED - do not use in new work. §2 permits
                       exactly one accent hue, and purple was decorative
                       (logo gradient, health bars, contributor palette).
                       Handoff.jsx (page 6) no longer uses it. Still held by
                       Settings.jsx (page 7, the Owner role pill) and by two
                       screens outside the redesign scope, AuthPage.jsx (Clerk
                       sign-in) and Landing.jsx. Deleting the token now emits
                       no style at all rather than failing loudly, so it stays
                       until page 7 lands and the two out-of-scope screens are
                       migrated deliberately. */
                    purple: '#A78BFA',
                },

                /* LAYER 2 — SEMANTIC. What a component should reach for. */
                surface: {
                    DEFAULT: primitive.neutral900,
                    page:    primitive.neutral950,
                    hover:   primitive.neutral850,
                },
                hairline: {
                    DEFAULT: primitive.neutral800,
                    hover:   primitive.neutral700,
                },
                content: {
                    DEFAULT:   primitive.neutral50,
                    secondary: primitive.neutral300,
                    /* label: readable micro-labels. NOT neutral500, which
                       measures 2.20:1 and fails WCAG. See tokens.css. */
                    label:     primitive.neutral300,
                    muted:     primitive.neutral500,
                },
                /* Named `brand`, NOT `accent`. shadcn's own components use
                   bg-accent / focus:bg-accent for neutral hover and focus
                   surfaces; overriding that key would turn every dropdown
                   item and menu highlight blue. */
                brand: {
                    DEFAULT: primitive.blue500,   /* links, active nav, bars   */
                    fill:    primitive.blue600,   /* solid buttons: 4.63:1 AA  */
                    hover:   primitive.blue400,
                },
                success: primitive.green500,
                danger:  primitive.red500,
                warning: primitive.amber500,

                /* shadcn tokens — untouched, 46 vendored components depend on
                   these resolving exactly as before. */
                background: 'hsl(var(--background))',
                foreground: 'hsl(var(--foreground))',
                card: {
                    DEFAULT: 'hsl(var(--card))',
                    foreground: 'hsl(var(--card-foreground))'
                },
                popover: {
                    DEFAULT: 'hsl(var(--popover))',
                    foreground: 'hsl(var(--popover-foreground))'
                },
                primary: {
                    DEFAULT: 'hsl(var(--primary))',
                    foreground: 'hsl(var(--primary-foreground))'
                },
                secondary: {
                    DEFAULT: 'hsl(var(--secondary))',
                    foreground: 'hsl(var(--secondary-foreground))'
                },
                muted: {
                    DEFAULT: 'hsl(var(--muted))',
                    foreground: 'hsl(var(--muted-foreground))'
                },
                accent: {
                    DEFAULT: 'hsl(var(--accent))',
                    foreground: 'hsl(var(--accent-foreground))'
                },
                destructive: {
                    DEFAULT: 'hsl(var(--destructive))',
                    foreground: 'hsl(var(--destructive-foreground))'
                },
                border: 'hsl(var(--border))',
                input: 'hsl(var(--input))',
                ring: 'hsl(var(--ring))',
                chart: {
                    '1': 'hsl(var(--chart-1))',
                    '2': 'hsl(var(--chart-2))',
                    '3': 'hsl(var(--chart-3))',
                    '4': 'hsl(var(--chart-4))',
                    '5': 'hsl(var(--chart-5))'
                }
            },
            keyframes: {
                'accordion-down': {
                    from: { height: '0' },
                    to: { height: 'var(--radix-accordion-content-height)' }
                },
                'accordion-up': {
                    from: { height: 'var(--radix-accordion-content-height)' },
                    to: { height: '0' }
                }
            },
            animation: {
                'accordion-down': 'accordion-down 0.2s ease-out',
                'accordion-up': 'accordion-up 0.2s ease-out'
            }
        }
    },
    plugins: [require("tailwindcss-animate")],
};
