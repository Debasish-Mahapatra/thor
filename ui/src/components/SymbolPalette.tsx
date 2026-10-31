// Symbols by group; a click puts the command at the cursor.

const GROUPS: [string, [string, string][]][] = [
  ["Greek", [["α", "\\alpha"], ["β", "\\beta"], ["γ", "\\gamma"], ["δ", "\\delta"], ["ε", "\\epsilon"], ["ζ", "\\zeta"], ["η", "\\eta"], ["θ", "\\theta"], ["κ", "\\kappa"], ["λ", "\\lambda"], ["μ", "\\mu"], ["ν", "\\nu"], ["ξ", "\\xi"], ["π", "\\pi"], ["ρ", "\\rho"], ["σ", "\\sigma"], ["τ", "\\tau"], ["φ", "\\phi"], ["χ", "\\chi"], ["ψ", "\\psi"], ["ω", "\\omega"], ["Γ", "\\Gamma"], ["Δ", "\\Delta"], ["Θ", "\\Theta"], ["Λ", "\\Lambda"], ["Π", "\\Pi"], ["Σ", "\\Sigma"], ["Φ", "\\Phi"], ["Ψ", "\\Psi"], ["Ω", "\\Omega"]]],
  ["Relations", [["≤", "\\leq"], ["≥", "\\geq"], ["≠", "\\neq"], ["≈", "\\approx"], ["≡", "\\equiv"], ["∼", "\\sim"], ["∝", "\\propto"], ["≪", "\\ll"], ["≫", "\\gg"], ["∈", "\\in"], ["⊂", "\\subset"]]],
  ["Operators", [["±", "\\pm"], ["×", "\\times"], ["·", "\\cdot"], ["÷", "\\div"], ["∑", "\\sum"], ["∏", "\\prod"], ["∫", "\\int"], ["∮", "\\oint"], ["∂", "\\partial"], ["∇", "\\nabla"], ["√", "\\sqrt{}"], ["∞", "\\infty"]]],
  ["Arrows", [["→", "\\rightarrow"], ["←", "\\leftarrow"], ["↔", "\\leftrightarrow"], ["⇒", "\\Rightarrow"], ["⇔", "\\Leftrightarrow"], ["↑", "\\uparrow"], ["↓", "\\downarrow"]]],
  ["Units and text", [["°", "^\\circ"], ["‰", "\\textperthousand"], ["µ", "\\textmu"], ["…", "\\ldots"], ["–", "--"], ["½", "\\frac{1}{2}"], ["x²", "^{2}"], ["x₂", "_{2}"], ["°C", "^\\circ\\mathrm{C}"], ["m s⁻¹", "\\mathrm{m\\,s^{-1}}"], ["W m⁻²", "\\mathrm{W\\,m^{-2}}"]]],
];

export function SymbolPalette({ onInsert }: { onInsert: (text: string) => void }) {
  return (
    <div className="symbols" aria-label="Symbols">
      {GROUPS.map(([group, symbols]) => (
        <div className="symbol-group" key={group}>
          <span className="muted small">{group}</span>
          {symbols.map(([glyph, command]) => (
            // Keeps the focus and the cursor in the editor.
            <button key={command} title={command} onMouseDown={(e) => e.preventDefault()} onClick={() => onInsert(command)}>
              {glyph}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
