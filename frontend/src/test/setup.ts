import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { unexpectedRequests } from './fetch'

afterEach(() => {
  cleanup()
  const unexpected = unexpectedRequests.splice(0)
  if (unexpected.length > 0) {
    throw new Error(`Requests without a mocked route: ${unexpected.join(', ')}`)
  }
})
