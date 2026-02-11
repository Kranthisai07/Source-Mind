const puppeteer = require('puppeteer');

async function run() {
    console.log('Launching browser for full preview...');
    const browser = await puppeteer.launch({
        headless: "new",
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();

    // Set larger viewport
    await page.setViewport({ width: 1280, height: 800 });

    try {
        // 1. Login
        console.log('--- Step 1: Login ---');
        await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle0' });
        await page.type('input[type="email"]', 'browser.test.1@example.com');
        await page.type('input[type="password"]', 'Password123!');
        await page.click('button[type="submit"]'); // This should trigger toast + redirect

        // Wait for dashboard H1
        try {
            await page.waitForSelector('h1', { timeout: 5000 });
        } catch (e) { console.log('Wait for H1 timeout, checking URL'); }

        // 2. Dashboard
        console.log('--- Step 2: Dashboard ---');
        await page.waitForTimeout(2000); // Wait for data/skeletons to resolve
        await page.screenshot({ path: 'preview-dashboard.png' });
        console.log('Captured preview-dashboard.png');

        // 3. Projects List
        // We need to click a workspace. Let's find a card link.
        // Assuming the first card is a link to /workspaces/:id/projects
        const workspaceCard = await page.$('a[href^="/workspaces/"]');
        if (workspaceCard) {
            console.log('--- Step 3: Projects List ---');
            await workspaceCard.click();
            await page.waitForNavigation({ waitUntil: 'networkidle0' });
            await page.waitForTimeout(2000); // Wait for skeletons
            await page.screenshot({ path: 'preview-projects.png' });
            console.log('Captured preview-projects.png');

            // 4. Memories List
            // Click first project
            const projectCard = await page.$('a[href*="/memories"]');
            if (projectCard) {
                console.log('--- Step 4: Memories List ---');
                await projectCard.click();
                await page.waitForSelector('input[placeholder="Search memories..."]', { timeout: 10000 });
                await page.waitForTimeout(2000); // Wait for list
                await page.screenshot({ path: 'preview-memories.png' });
                console.log('Captured preview-memories.png');
            } else {
                console.log('No project card found to click.');
            }
        } else {
            console.log('No workspace card found to click. Creating one?');
            // Optional: Create workspace logic if needed, but assuming data exists from previous tests
        }

    } catch (e) {
        console.error('Preview failed:', e);
    } finally {
        await browser.close();
    }
}

run();
