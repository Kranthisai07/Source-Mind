describe("appUrl", () => {
    const originalPublicUrl = process.env.PUBLIC_URL;

    afterEach(() => {
        process.env.PUBLIC_URL = originalPublicUrl;
        jest.resetModules();
    });

    test("prefixes asset and redirect paths with the GitHub Pages basename", () => {
        process.env.PUBLIC_URL = "/Source-Mind/";
        jest.resetModules();

        const { appUrl, BASENAME } = require("./appUrl");

        expect(BASENAME).toBe("/Source-Mind");
        expect(appUrl("/images/project-intelligence-light.webp")).toBe(
            "/Source-Mind/images/project-intelligence-light.webp"
        );
        expect(appUrl("dashboard")).toBe("/Source-Mind/dashboard");
    });

    test("keeps origin-relative paths when no basename is configured", () => {
        process.env.PUBLIC_URL = "";
        jest.resetModules();

        const { appUrl, BASENAME } = require("./appUrl");

        expect(BASENAME).toBe("");
        expect(appUrl("/dashboard")).toBe("/dashboard");
    });
});
