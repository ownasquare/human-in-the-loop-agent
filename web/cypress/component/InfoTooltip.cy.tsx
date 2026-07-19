import { mount } from "cypress/react";
import { InfoTooltip } from "../../src/components/InfoTooltip";

describe("InfoTooltip", () => {
  it("reveals compact help on keyboard focus and exposes the description relationship", () => {
    mount(
      <InfoTooltip label="About demo mode">
        Demo mode uses local fixtures and never contacts a live provider.
      </InfoTooltip>,
    );

    cy.get('button[aria-label="About demo mode"]')
      .should("have.attr", "aria-describedby")
      .then((describedBy) => expect(describedBy).not.to.be.empty);
    cy.get('[role="tooltip"]').should("not.be.visible");
    cy.get('button[aria-label="About demo mode"]').focus();
    cy.get('[role="tooltip"]')
      .should("be.visible")
      .and("contain.text", "never contacts a live provider");
    cy.get('button[aria-label="About demo mode"]').blur();
    cy.get('[role="tooltip"]').should("not.be.visible");
  });
});
