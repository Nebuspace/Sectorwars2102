import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import dns from 'dns'

// This is critical: configure DNS to use IPv4 instead of IPv6
dns.setDefaultResultOrder('ipv4first')

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // When this UI is served behind nginx-gateway under `/admin/` (stage,
  // prod), Vite must emit asset paths prefixed with `/admin/` so the
  // browser fetches `/admin/src/main.tsx` (which nginx routes here)
  // instead of `/src/main.tsx` (which nginx routes to player-client).
  // Local dev hits this server at the root, so leave the default in
  // that case. Set `VITE_BASE=/admin/` in the stage/prod env to flip.
  base: process.env.VITE_BASE || '/',
  server: {
    host: true, // Listen on all addresses
    port: 3000, // Fixed to match Docker port mapping
    strictPort: true,
    https: false, // Explicitly disable HTTPS

    // HMR configuration - disable in Codespaces due to port forwarding issues
    hmr: process.env.CODESPACE_NAME ? false : true,

    // Direct configuration to allow any host
    cors: true,

    // Add explicit wildcard for all hosts
    allowedHosts: true,

    // Don't check origin at all
    origin: '*',

    // Add proxy for API server to bypass CORS issues across all environments
    proxy: {
      '/api': {
        target: process.env.API_URL || 'http://gameserver:8080',
        changeOrigin: true,
        secure: false,
        ws: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (proxyReq, req, _res) => {
            console.log('Sending Request to the Target:', req.method, req.url);
            // For GitHub Codespaces, preserve the original host header
            if (process.env.CODESPACE_NAME || process.env.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN) {
              if (req.headers.host) {
                proxyReq.setHeader('host', req.headers.host);
              }
            }
          });
          proxy.on('proxyRes', (proxyRes, req, _res) => {
            console.log('Received Response from the Target:', proxyRes.statusCode, req.url);
          });
        },
      }
    },

    watch: {
      usePolling: true,
      // Enhanced file watching for Codespaces
      interval: process.env.CODESPACE_NAME ? 1000 : 100, // Faster polling in Codespaces
      binaryInterval: 1000,
    },

    // Disable FS restriction
    fs: {
      strict: false,
      allow: ['..'],
    },
  },
})