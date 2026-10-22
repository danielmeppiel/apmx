// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// Deployed to GitHub Pages as a project site: https://danielmeppiel.github.io/apmx/
export default defineConfig({
  site: 'https://danielmeppiel.github.io',
  base: '/apmx',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'APMX',
      description: 'APMX runs agentic software factories built from Agent Contracts and ships every change with signed evidence of how it was made.',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/danielmeppiel/apmx' }],
      customCss: [
        '@fontsource-variable/mona-sans/wdth.css',
        '@fontsource/monaspace-neon/400.css',
        '@fontsource/monaspace-neon/600.css',
        './src/styles/tokens.css',
        './src/styles/starlight.css',
      ],
      expressiveCode: {
        themes: ['github-dark'],
        styleOverrides: {
          codeFontFamily: "'Monaspace Neon', ui-monospace, SFMono-Regular, Menlo, monospace",
          codeFontSize: '0.875rem',
          borderRadius: '14px',
          borderColor: '#3d444d',
          frames: { terminalTitlebarDotsOpacity: '0', frameBoxShadowCssValue: 'none' },
        },
      },
      sidebar: [
        {
          label: 'Docs',
          items: [
            { label: 'Write an Agent Contract', slug: 'docs/agent-contracts' },
            { label: 'Run a factory on any harness', slug: 'docs/any-harness' },
            { label: 'Read the receipt', slug: 'docs/receipt' },
            { label: 'Auto-merge factory PRs on trusted evidence', slug: 'docs/accept-prs' },
            { label: 'FAQ', slug: 'docs/faq' },
          ],
        },
        {
          label: 'In the repo',
          items: [
            { label: 'Install', link: 'https://github.com/danielmeppiel/apmx/blob/main/docs/install.md' },
            { label: 'OpenCode profile', link: 'https://github.com/danielmeppiel/apmx/blob/main/docs/opencode.md' },
            { label: 'Results and exit codes', link: 'https://github.com/danielmeppiel/apmx/blob/main/docs/results.md' },
            { label: 'Audit reference', link: 'https://github.com/danielmeppiel/apmx/blob/main/docs/audit.md' },
            { label: 'Source origin', link: 'https://github.com/danielmeppiel/apmx/blob/main/docs/source-origin.md' },
          ],
        },
      ],
    }),
  ],
});
