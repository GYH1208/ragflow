import '@testing-library/jest-dom';

// React Router 7 expects browser text encoding APIs, absent from jsdom.
import { TextDecoder, TextEncoder } from 'node:util';
Object.assign(globalThis, { TextDecoder, TextEncoder });

// esbuild-jest uses classic JSX for application modules.
import * as React from 'react';
Object.assign(globalThis, { React });
