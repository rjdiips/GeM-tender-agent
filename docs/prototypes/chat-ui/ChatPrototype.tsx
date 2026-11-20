// PROTOTYPE: mounts one chat-page variant, chosen by ?variant=A|B|C|D|E. All share variant A's layout.
import { useState } from 'react'
import { PrototypeSwitcher } from './PrototypeSwitcher'
import VariantA, { name as nameA } from './VariantA'
import VariantB, { name as nameB } from './VariantB'
import VariantC, { name as nameC } from './VariantC'
import VariantD, { name as nameD } from './VariantD'

const VariantCLight = () => <VariantC theme="light" />
const variants = { A: VariantA, B: VariantB, C: VariantC, D: VariantD, E: VariantCLight }
const names = { A: nameA, B: nameB, C: nameC, D: nameD, E: `${nameC} · light` }

export default function ChatPrototype() {
  const initial = new URLSearchParams(location.search).get('variant') ?? 'A'
  const [variant, setVariant] = useState(initial in variants ? initial : 'A')
  const change = (v: string) => {
    const url = new URL(location.href)
    url.searchParams.set('variant', v)
    history.replaceState(null, '', url)
    setVariant(v)
  }
  const View = variants[variant as keyof typeof variants]
  return (
    <>
      <View key={variant} />
      <PrototypeSwitcher variants={Object.keys(variants)} current={variant} names={names} onChange={change} />
    </>
  )
}
