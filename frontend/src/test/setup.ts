import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'
import { cloudAuth } from '../auth/client'

beforeEach(() => cloudAuth.usePersonal())

afterEach(cleanup)
