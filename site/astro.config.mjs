// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// Deployed to GitHub Pages as a project site: https://danielmeppiel.github.io/apmx/
export default defineConfig({
  site: 'https://danielmeppiel.github.io',
  base: '/apmx',
  trailingSlash: 'always',
  // Static output: Astro writes a meta-refresh page. The destination is not
  // base-prefixed by Astro, so it includes /apmx/ and the trailing slash.
  redirects: {
    '/docs': '/apmx/docs/quickstart/',
  },
  integrations: [
    starlight({
      title: 'APMX',
      description: 'APMX runs agentic software factories built from Agent Contracts and records how every change was made in a receipt a trusted signer can sign.',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/danielmeppiel/apmx' }],
      customCss: [
        '@fontsource-variable/mona-sans/wdth.css',
        '@fontsource/monaspace-neon/400.css',
        '@fontsource/monaspace-neon/600.css',
        './src/styles/tokens.css',
        './src/styles/starlight.css',
        './src/styles/docs.css',
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
          label: 'Get started',
          items: [
            { label: 'Install', slug: 'docs/install' },
            { label: 'First run', slug: 'docs/quickstart' },
          ],
        },
        {
          label: 'Concepts',
          items: [
            { label: 'Factories', slug: 'docs/factories' },
            { label: 'Agent Contracts', slug: 'docs/agent-contracts' },
            { label: 'The receipt', slug: 'docs/receipt' },
            { label: 'Trust model', slug: 'docs/trust-model' },
            { label: 'Audit verdicts', slug: 'docs/verdicts' },
          ],
        },
        {
          label: 'How-to guides',
          items: [
            { label: 'Choose a harness and model', slug: 'docs/any-harness' },
            { label: 'Audit a receipt', slug: 'docs/audit-a-receipt' },
            { label: 'Run a factory in CI', slug: 'docs/run-in-ci' },
            { label: 'Auto-merge factory PRs', slug: 'docs/accept-prs' },
            { label: 'Approve, upgrade, revoke', slug: 'docs/manage-policy' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'apmx', slug: 'docs/reference/apmx' },
            { label: 'apmx audit', slug: 'docs/reference/audit' },
            { label: 'apmx check', slug: 'docs/reference/check' },
            { label: 'Contract file', slug: 'docs/reference/contract' },
            { label: 'Policy (factories)', slug: 'docs/reference/policy' },
            { label: 'run.yml and actions', slug: 'docs/reference/run-yml' },
            { label: 'Receipt format', slug: 'docs/reference/receipt-format' },
            { label: 'Changelog', link: 'https://github.com/danielmeppiel/apmx/blob/main/CHANGELOG.md' },
          ],
        },
        { label: 'FAQ', slug: 'docs/faq' },
      ],
    }),
  ],
});
