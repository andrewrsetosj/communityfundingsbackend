import type { NextApiRequest, NextApiResponse } from 'next';

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:4000';

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  try {
    // forward to backend path after /api/proxy
    const backendPath = req.url?.replace('/api/proxy', '') || '';
    const backendUrl = `${BACKEND_URL}${backendPath}`;

    const headers: Record<string, string> = {};
    if (req.headers.authorization) headers['authorization'] = req.headers.authorization as string;
    if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'] as string;

    const fetchRes = await fetch(backendUrl, {
      method: req.method,
      headers,
      body: ['GET','HEAD'].includes(req.method || '') ? undefined : JSON.stringify(req.body),
    });

    const text = await fetchRes.text();
    res.status(fetchRes.status);
    // copy response headers (omit hop-by-hop)
    fetchRes.headers.forEach((value, key) => {
      if (['transfer-encoding','content-encoding','connection'].includes(key.toLowerCase())) return;
      res.setHeader(key, value);
    });
    res.send(text);
  } catch (err: any) {
    console.error('proxy error', err);
    res.status(500).json({ error: 'proxy error', details: String(err) });
  }
}
