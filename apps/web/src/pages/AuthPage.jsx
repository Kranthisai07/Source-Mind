import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Brain, Github, Mail, Lock } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";

export default function AuthPage({ mode = "sign-in" }) {
    const isSignUp = mode === "sign-up";
    const navigate = useNavigate();
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");

    const handleSubmit = (e) => {
        e.preventDefault();
        // Mocked auth — straight to dashboard.
        navigate("/dashboard");
    };

    return (
        <div className="min-h-screen bg-sm-bg relative flex items-center justify-center p-6 overflow-hidden">
            <div className="absolute inset-0 bg-grid bg-grid-fade opacity-30" />
            <div className="absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_60%_40%_at_50%_0%,rgba(79,126,255,0.15),transparent_70%)] pointer-events-none" />

            <div className="relative w-full max-w-[420px]">
                <Link to="/" className="flex items-center justify-center gap-2.5 mb-8 group" data-testid="auth-logo-link">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center shadow-[0_8px_24px_-8px_rgba(79,126,255,0.6)] group-hover:scale-105 transition-transform">
                        <Brain className="w-5 h-5 text-white" strokeWidth={2.5} />
                    </div>
                    <span className="text-[18px] font-semibold tracking-tight text-sm-text">SourceMind</span>
                </Link>
                <p className="text-center text-sm-text-secondary text-[13px] mb-10">Your team's collective memory</p>

                <div className="glass rounded-2xl p-8">
                    <h1 className="text-[22px] font-semibold text-sm-text mb-1 tracking-tight">
                        {isSignUp ? "Create your account" : "Welcome back"}
                    </h1>
                    <p className="text-[12.5px] text-sm-text-secondary mb-7">
                        {isSignUp ? "Start organizing your team's knowledge." : "Sign in to continue to your workspace."}
                    </p>

                    <Button
                        data-testid="auth-github-btn"
                        onClick={() => navigate("/dashboard")}
                        className="w-full h-11 mb-3 bg-white text-[#0A0A0F] hover:bg-white/90 font-medium"
                    >
                        <Github className="w-4 h-4 mr-2" />
                        Continue with GitHub
                    </Button>
                    <Button
                        data-testid="auth-google-btn"
                        variant="outline"
                        onClick={() => navigate("/dashboard")}
                        className="w-full h-11 mb-6 bg-white/[0.03] hover:bg-white/[0.06] border-sm-border text-sm-text"
                    >
                        <svg className="w-4 h-4 mr-2" viewBox="0 0 24 24">
                            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.09z" />
                            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.76c-.98.66-2.24 1.06-3.72 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0012 23z" />
                            <path fill="#FBBC05" d="M5.84 14.09A6.6 6.6 0 015.5 12c0-.73.13-1.44.34-2.09V7.07H2.18A11 11 0 001 12c0 1.77.42 3.44 1.18 4.93l3.66-2.84z" />
                            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 002.18 7.07l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z" />
                        </svg>
                        Continue with Google
                    </Button>

                    <div className="relative my-4 flex items-center">
                        <div className="flex-1 h-px bg-sm-border" />
                        <span className="px-3 text-[11px] font-mono text-sm-text-muted tracking-wider uppercase">or email</span>
                        <div className="flex-1 h-px bg-sm-border" />
                    </div>

                    <form onSubmit={handleSubmit} className="space-y-3">
                        <div className="relative">
                            <Mail className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-sm-text-muted" />
                            <Input
                                data-testid="auth-email-input"
                                type="email"
                                required
                                placeholder="you@company.dev"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="h-11 pl-9 bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted"
                            />
                        </div>
                        <div className="relative">
                            <Lock className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-sm-text-muted" />
                            <Input
                                data-testid="auth-password-input"
                                type="password"
                                required
                                placeholder="••••••••"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="h-11 pl-9 bg-sm-bg/60 border-sm-border text-sm-text placeholder:text-sm-text-muted"
                            />
                        </div>
                        <Button
                            data-testid="auth-submit-btn"
                            type="submit"
                            className="w-full h-11 bg-sm-blue hover:bg-sm-blue/90 text-white font-medium"
                        >
                            {isSignUp ? "Create account" : "Sign in"}
                        </Button>
                    </form>

                    <div className="mt-6 text-center text-[12px] text-sm-text-secondary">
                        {isSignUp ? (
                            <>Already have an account? <Link to="/sign-in" className="text-sm-blue hover:underline" data-testid="switch-to-signin">Sign in</Link></>
                        ) : (
                            <>Don't have an account? <Link to="/sign-up" className="text-sm-blue hover:underline" data-testid="switch-to-signup">Sign up</Link></>
                        )}
                    </div>
                </div>

                <p className="text-center text-[11px] font-mono text-sm-text-muted mt-6 tracking-wider">
                    PROTECTED BY CLERK · SOC 2 TYPE II
                </p>
            </div>
        </div>
    );
}
