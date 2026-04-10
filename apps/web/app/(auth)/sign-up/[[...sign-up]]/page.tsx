import { SignUp } from '@clerk/nextjs'

export default function SignUpPage() {
  return (
    <div className="min-h-screen bg-bg flex items-center justify-center">
      <div className="w-full max-w-md px-4">
        <div className="mb-8 text-center">
          <h1 className="font-display text-4xl gradient-text mb-2">SourceMind</h1>
          <p className="text-secondary text-sm">Collaborative team memory platform</p>
        </div>
        <SignUp />
      </div>
    </div>
  )
}
