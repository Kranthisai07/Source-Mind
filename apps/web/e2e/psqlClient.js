/**
 * A portable `psql` invocation for the integration suite.
 *
 * The suite previously shelled out through `wsl.exe` unconditionally, with the
 * port, role, database and password path all hardcoded to one developer
 * machine. That made the harness unrunnable anywhere else — including on a CI
 * runner, where `psql` is on PATH and there is no WSL at all.
 *
 * Two things this must not lose in becoming portable:
 *
 *   1. **The disposable-target guard.** The seed deletes every memory and
 *      conflict in its workspace. Pointed at a real database it would destroy
 *      data, and the old hardcoding was the only thing preventing that. The
 *      guard is now explicit and fails closed: it demands an opt-in flag AND a
 *      database name that looks disposable, and it refuses anything that looks
 *      like production regardless of the flag.
 *
 *   2. **Secret redaction.** The password never appears in argv — it is passed
 *      to the child through PGPASSWORD in its environment, so it cannot show up
 *      in a process listing. Anything thrown from here is scrubbed, because a
 *      psql failure otherwise echoes the connection it attempted.
 *
 * Configuration, all via environment:
 *
 *   E2E_ALLOW_DISPOSABLE   required, must be "1". Refuses to run otherwise.
 *   E2E_PG_HOST            default 127.0.0.1
 *   E2E_PG_PORT            default 5432
 *   E2E_PG_USER            required
 *   E2E_PG_PASSWORD        optional (omit for trust/peer auth)
 *   E2E_PG_DATABASE        required, must satisfy the disposable guard
 *   E2E_PSQL               psql executable. Default "psql".
 *   E2E_PSQL_MODE          "direct" | "wsl". Default: "wsl" on win32 when no
 *                          psql is on PATH, "direct" everywhere else.
 */

const { execFileSync } = require("child_process");

/**
 * A database name must match one of these to be considered disposable.
 * Deliberately narrow: "looks like it was made to be thrown away".
 */
const DISPOSABLE_NAME = /(^|[_-])(e2e|test|tests|disposable|scratch|ci)([_-]|$)/i;

/**
 * And must match none of these, whatever the flag says. A wrong environment
 * variable should not be able to arm a destructive seed against production.
 */
const FORBIDDEN_NAME = /(prod|production|live|main|master|staging)/i;

function redact(text, secret) {
    const s = String(text ?? "");
    if (!secret) return s;
    // Split/join rather than a RegExp: the password is arbitrary bytes and may
    // contain regex metacharacters.
    return s.split(secret).join("«redacted»");
}

function config(env = process.env) {
    const database = env.E2E_PG_DATABASE || "";
    const user = env.E2E_PG_USER || "";

    if (env.E2E_ALLOW_DISPOSABLE !== "1") {
        throw new Error(
            "refusing to run: E2E_ALLOW_DISPOSABLE is not \"1\". This suite " +
            "deletes every memory and conflict in its workspace, so it only " +
            "runs against a target explicitly marked disposable."
        );
    }
    if (!database) throw new Error("refusing to run: E2E_PG_DATABASE is not set.");
    if (!user) throw new Error("refusing to run: E2E_PG_USER is not set.");
    if (FORBIDDEN_NAME.test(database)) {
        throw new Error(
            `refusing to run: database ${JSON.stringify(database)} matches a ` +
            "protected name. The disposable flag does not override this."
        );
    }
    if (!DISPOSABLE_NAME.test(database)) {
        throw new Error(
            `refusing to run: database ${JSON.stringify(database)} is not ` +
            "recognisably disposable. Name it with an e2e/test/ci marker."
        );
    }

    return {
        host: env.E2E_PG_HOST || "127.0.0.1",
        port: env.E2E_PG_PORT || "5432",
        user,
        password: env.E2E_PG_PASSWORD || "",
        database,
        psql: env.E2E_PSQL || "psql",
        mode: resolveMode(env),
    };
}

function resolveMode(env) {
    const explicit = (env.E2E_PSQL_MODE || "").toLowerCase();
    if (explicit === "direct" || explicit === "wsl") return explicit;
    if (explicit) {
        throw new Error(`E2E_PSQL_MODE must be "direct" or "wsl", got ${JSON.stringify(explicit)}`);
    }
    // A CI runner has psql on PATH and no WSL; the development machine here is
    // the reverse. Default by platform, and let the variable override.
    return process.platform === "win32" ? "wsl" : "direct";
}

/**
 * Run one query and return psql's unaligned, tuples-only output.
 *
 * Booleans render as `t` / `f`, and a NULL renders as the EMPTY STRING — not
 * as `f`, and not as the word "null". Callers that care about nullness should
 * select an explicit sentinel rather than compare against a rendering.
 */
function createPsql(env = process.env, exec = execFileSync) {
    // `exec` is injectable for one reason: CI runs "direct" mode exclusively,
    // and this machine has no psql outside WSL, so that path would otherwise
    // ship having never been executed. Injecting a recorder lets the argv and
    // environment be asserted — in particular that the password is in the
    // child's environment and never in argv — without a database.
    const cfg = config(env);

    return function sql(query) {
        const args = [
            "-h", cfg.host,
            "-p", String(cfg.port),
            "-U", cfg.user,
            "-d", cfg.database,
            "-v", "ON_ERROR_STOP=1",
            "-Atc", query,
        ];

        let file, argv, childEnv;

        if (cfg.mode === "direct") {
            file = cfg.psql;
            argv = args;
            // PGPASSWORD in the child's environment, never in argv.
            childEnv = { ...process.env, PGPASSWORD: cfg.password };
        } else {
            // WSL: the password crosses as an environment variable too. It is
            // assigned inside the guest shell from an exported name rather
            // than interpolated into the command string, so it never becomes
            // part of a command line on either side of the boundary.
            file = "wsl.exe";
            argv = [
                "-e", "env", `PGPASSWORD=${cfg.password}`, cfg.psql, ...args,
            ];
            childEnv = { ...process.env };
        }

        try {
            const out = exec(file, argv, {
                encoding: "utf8",
                stdio: ["ignore", "pipe", "pipe"],
                env: childEnv,
            });
            return out.replace(/\r/g, "").trim();
        } catch (err) {
            // psql echoes the connection it attempted, and execFileSync
            // attaches the full argv. Scrub both before this escapes.
            const detail = redact(
                [err.stderr, err.stdout, err.message].filter(Boolean).join("\n"),
                cfg.password
            );
            const e = new Error(
                `psql (${cfg.mode}) failed for query: ${query}\n${detail}`
            );
            e.stack = redact(e.stack, cfg.password);
            throw e;
        }
    };
}

module.exports = {
    createPsql,
    // Exported for the guard's own tests. Not part of the suite's interface.
    _internals: { config, resolveMode, redact, DISPOSABLE_NAME, FORBIDDEN_NAME },
};
