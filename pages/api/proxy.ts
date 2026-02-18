// pages/api/proxy.ts
import type { NextApiRequest, NextApiResponse } from 'next';

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:4000';

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  try {
    const backendUrl = `${BACKEND_URL}${req.url?.replace('/api/proxy', '') || ''}`;
    // Build headers: forward Authorization and content-type
    const headers: Record<string, string> = {};
    if (req.headers.authorization) {
      headers['authorization'] = req.headers.authorization as string;
    }
    if (req.headers['content-type']) {
      headers['content-type'] = req.headers['content-type'] as string;
    } else {
      headers['content-type'] = 'application/json';
    }

    const fetchRes = await fetch(backendUrl, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method || '') ? undefined : JSON.stringify(req.body),
    });

    const text = await fetchRes.text();
    // Proxy status and headers (filter hop-by-hop headers)
    res.status(fetchRes.status);
    // Copy selected headers
    fetchRes.headers.forEach((value, key) => {
      // avoid hop-by-hop headers
      if (['transfer-encoding', 'content-encoding', 'content-length', 'connection'].includes(key.toLowerCase())) return;
      res.setHeader(key, value);
    });
    // Send body
    res.send(text);
  } catch (err: any) {
    console.error('proxy error', err);
    res.status(500).json({ error: 'proxy error', details: String(err) });
  }
}
