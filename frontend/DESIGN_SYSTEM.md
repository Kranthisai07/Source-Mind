# SourceMind Design System

## Overview

This design system provides a comprehensive set of design tokens, components, and utilities for building consistent, accessible, and beautiful interfaces for SourceMind.

---

## 🎨 Color Palette

### Primary Colors (Deep Blue - Trust, Intelligence)
- **50-100**: Light backgrounds, subtle highlights
- **500-600**: Primary actions, links, focus states
- **700-900**: Text, dark backgrounds

### Secondary Colors (Purple - Innovation, AI)
- Used for AI-related features, secondary actions
- Represents intelligence and innovation

### Accent Colors (Cyan - Energy, Collaboration)
- Highlights, interactive elements
- Represents collaboration and energy

### Semantic Colors
- **Success**: Green (#22c55e)
- **Warning**: Amber (#f59e0b)
- **Error**: Red (#ef4444)
- **Info**: Blue (#3b82f6)

### Attribution Colors
- **Human**: Blue (`--color-human: #3b82f6`)
- **AI**: Purple (`--color-ai: #a855f7`)
- **Mixed**: Cyan (`--color-mixed: #06b6d4`)

---

## 📝 Typography

### Font Families
- **Sans-serif**: Inter (primary)
- **Monospace**: JetBrains Mono (code)

### Font Sizes
```css
--text-xs: 0.75rem;    /* 12px */
--text-sm: 0.875rem;   /* 14px */
--text-base: 1rem;     /* 16px */
--text-lg: 1.125rem;   /* 18px */
--text-xl: 1.25rem;    /* 20px */
--text-2xl: 1.5rem;    /* 24px */
--text-3xl: 1.875rem;  /* 30px */
--text-4xl: 2.25rem;   /* 36px */
--text-5xl: 3rem;      /* 48px */
```

### Font Weights
- **Normal**: 400
- **Medium**: 500
- **Semibold**: 600
- **Bold**: 700

---

## 🧱 Components

### Buttons

#### Variants
```html
<!-- Primary -->
<button class="btn btn-primary">Primary Button</button>

<!-- Secondary -->
<button class="btn btn-secondary">Secondary Button</button>

<!-- Outline -->
<button class="btn btn-outline">Outline Button</button>

<!-- Ghost -->
<button class="btn btn-ghost">Ghost Button</button>
```

#### Sizes
```html
<button class="btn btn-primary btn-sm">Small</button>
<button class="btn btn-primary">Default</button>
<button class="btn btn-primary btn-lg">Large</button>
```

#### States
```html
<button class="btn btn-primary" disabled>Disabled</button>
```

---

### Cards

```html
<!-- Basic Card -->
<div class="card">
  <div class="card-header">
    <h3 class="card-title">Card Title</h3>
    <p class="card-description">Card description</p>
  </div>
  <div class="card-content">
    <p>Card content goes here</p>
  </div>
  <div class="card-footer">
    <button class="btn btn-sm btn-outline">Action</button>
  </div>
</div>

<!-- Interactive Card -->
<div class="card card-interactive">
  Clickable card with hover effects
</div>
```

---

### Inputs

```html
<!-- Text Input -->
<div class="input-group">
  <label class="label" for="email">Email</label>
  <input type="email" id="email" class="input" placeholder="Enter your email" />
  <span class="input-hint">We'll never share your email</span>
</div>

<!-- Textarea -->
<div class="input-group">
  <label class="label" for="message">Message</label>
  <textarea id="message" class="input textarea" placeholder="Your message"></textarea>
</div>

<!-- Error State -->
<div class="input-group">
  <label class="label" for="username">Username</label>
  <input type="text" id="username" class="input input-error" />
  <span class="input-error-message">Username is required</span>
</div>
```

---

### Badges

```html
<!-- Variants -->
<span class="badge badge-primary">Primary</span>
<span class="badge badge-secondary">Secondary</span>
<span class="badge badge-success">Success</span>
<span class="badge badge-warning">Warning</span>
<span class="badge badge-error">Error</span>

<!-- Attribution Badges -->
<span class="badge badge-human">Human</span>
<span class="badge badge-ai">AI</span>
<span class="badge badge-mixed">Mixed</span>
```

---

### Alerts

```html
<div class="alert alert-info">
  This is an informational message
</div>

<div class="alert alert-success">
  Success! Your changes have been saved
</div>

<div class="alert alert-warning">
  Warning: Please review your input
</div>

<div class="alert alert-error">
  Error: Something went wrong
</div>
```

---

### Loading States

```html
<!-- Skeleton Loader -->
<div class="skeleton" style="height: 20px; width: 200px;"></div>

<!-- Spinner -->
<div class="spinner"></div>
```

---

## 🛠️ Utility Classes

### Layout

```html
<!-- Container -->
<div class="container">Full width container (max 1280px)</div>
<div class="container container-sm">Small container (max 640px)</div>

<!-- Flexbox -->
<div class="flex items-center justify-between gap-4">
  <div>Item 1</div>
  <div>Item 2</div>
</div>

<!-- Grid -->
<div class="grid grid-cols-3 gap-4">
  <div>Column 1</div>
  <div>Column 2</div>
  <div>Column 3</div>
</div>
```

### Spacing

```html
<!-- Margin -->
<div class="m-4">Margin all sides</div>
<div class="mt-4">Margin top</div>
<div class="mb-4">Margin bottom</div>

<!-- Padding -->
<div class="p-4">Padding all sides</div>
```

### Typography

```html
<p class="text-sm text-secondary">Small secondary text</p>
<p class="text-lg font-semibold text-primary">Large semibold primary text</p>
<p class="text-center uppercase">Centered uppercase text</p>
```

### Borders & Shadows

```html
<div class="rounded-lg border shadow-md">
  Card with border and shadow
</div>
```

---

## 🌓 Dark Mode

### Enabling Dark Mode

```typescript
// Set dark mode
document.documentElement.setAttribute('data-theme', 'dark');

// Set light mode
document.documentElement.setAttribute('data-theme', 'light');

// Toggle
const currentTheme = document.documentElement.getAttribute('data-theme');
document.documentElement.setAttribute('data-theme', currentTheme === 'dark' ? 'light' : 'dark');
```

### Dark Mode Colors

Dark mode automatically adjusts:
- Background colors
- Text colors
- Border colors
- Component styles

---

## 📱 Responsive Design

### Breakpoints

- **sm**: max-width 640px (mobile)
- **md**: min-width 768px (tablet)
- **lg**: min-width 1024px (desktop)

### Usage

```html
<!-- Hide on mobile -->
<div class="sm:hidden">Hidden on mobile</div>

<!-- Responsive grid -->
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
  <!-- Columns adapt to screen size -->
</div>
```

---

## ♿ Accessibility

### Focus States
All interactive elements have visible focus indicators

### Screen Reader Support
```html
<span class="sr-only">Screen reader only text</span>
```

### Reduced Motion
Respects `prefers-reduced-motion` user preference

---

## 🎯 Best Practices

### 1. Use Design Tokens
Always use CSS custom properties instead of hard-coded values:

```css
/* ✅ Good */
color: var(--color-primary-600);

/* ❌ Bad */
color: #2563eb;
```

### 2. Semantic HTML
Use appropriate HTML elements:

```html
<!-- ✅ Good -->
<button class="btn btn-primary">Click me</button>

<!-- ❌ Bad -->
<div class="btn btn-primary" onclick="...">Click me</div>
```

### 3. Consistent Spacing
Use the spacing scale for consistency:

```css
/* ✅ Good */
margin-bottom: var(--space-4);

/* ❌ Bad */
margin-bottom: 17px;
```

### 4. Component Composition
Build complex UIs by composing simple components:

```html
<div class="card">
  <div class="flex items-center justify-between mb-4">
    <h3 class="text-xl font-semibold">Title</h3>
    <span class="badge badge-primary">New</span>
  </div>
  <p class="text-secondary">Content</p>
</div>
```

---

## 🚀 Usage Examples

### Memory Card

```html
<div class="card card-interactive">
  <div class="flex items-start justify-between mb-3">
    <div>
      <span class="badge badge-neutral text-xs uppercase">Decision</span>
      <h3 class="text-lg font-semibold mt-2">OAuth Implementation</h3>
    </div>
    <span class="text-xs text-tertiary">2 hours ago</span>
  </div>
  
  <p class="text-secondary mb-4">
    We should implement OAuth 2.0 for user authentication...
  </p>
  
  <div class="flex items-center gap-2">
    <span class="badge badge-human">Sarah - 65%</span>
    <span class="badge badge-ai">AI - 25%</span>
    <span class="badge badge-human">John - 10%</span>
  </div>
</div>
```

### Login Form

```html
<div class="container container-sm">
  <div class="card">
    <h1 class="text-3xl font-bold mb-2">Welcome Back</h1>
    <p class="text-secondary mb-6">Sign in to your account</p>
    
    <form>
      <div class="input-group">
        <label class="label" for="email">Email</label>
        <input type="email" id="email" class="input" placeholder="you@example.com" />
      </div>
      
      <div class="input-group">
        <label class="label" for="password">Password</label>
        <input type="password" id="password" class="input" placeholder="••••••••" />
      </div>
      
      <button type="submit" class="btn btn-primary w-full">
        Sign In
      </button>
    </form>
  </div>
</div>
```

### Dashboard Grid

```html
<div class="container">
  <h1 class="text-4xl font-bold mb-8">Dashboard</h1>
  
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    <div class="card">
      <h3 class="text-sm font-medium text-tertiary uppercase mb-2">Total Memories</h3>
      <p class="text-3xl font-bold text-primary">1,234</p>
    </div>
    
    <div class="card">
      <h3 class="text-sm font-medium text-tertiary uppercase mb-2">Contributors</h3>
      <p class="text-3xl font-bold text-primary">12</p>
    </div>
    
    <div class="card">
      <h3 class="text-sm font-medium text-tertiary uppercase mb-2">AI Contribution</h3>
      <p class="text-3xl font-bold text-secondary">35%</p>
    </div>
  </div>
</div>
```

---

## 📦 Installation

The design system is already included in your project via `globals.css`. Simply import it in your layout:

```typescript
// app/layout.tsx
import './globals.css';
```

---

## 🔄 Updates

When updating the design system:

1. Modify `frontend/styles/globals.css`
2. Update this documentation
3. Test in both light and dark modes
4. Ensure accessibility compliance
5. Update component examples

---

## 📚 Resources

- **Color Palette**: Based on Tailwind CSS color system
- **Typography**: Inter font from Google Fonts
- **Icons**: Recommend Lucide React or Heroicons
- **Inspiration**: Modern SaaS applications, Vercel, Linear

---

**Version**: 1.0  
**Last Updated**: January 23, 2026  
**Maintained By**: SourceMind Team
