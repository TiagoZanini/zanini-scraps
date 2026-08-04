/**
 * Zanini Scraps - JavaScript Principal
 * Desenvolvido por Grupo Zanini S.A.
 */

// Auto-dismiss alerts
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.alert').forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            alert.style.transform = 'translateY(-10px)';
            setTimeout(() => alert.remove(), 300);
        }, 5000);
    });

    // Search suggestions
    const searchInput = document.querySelector('.hero-search input[name="q"]');
    if (searchInput) {
        let timeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                if (this.value.length >= 2) {
                    fetch('/api/search-suggestions?q=' + encodeURIComponent(this.value))
                        .then(r => r.json())
                        .then(data => {
                            let box = document.getElementById('search-suggestions');
                            if (!box) {
                                box = document.createElement('div');
                                box.id = 'search-suggestions';
                                box.style.cssText = 'position:absolute;top:100%;left:0;right:0;background:var(--bg-card);border:1px solid var(--border);border-radius:0 0 12px 12px;z-index:50;max-height:300px;overflow-y:auto;';
                                searchInput.parentElement.style.position = 'relative';
                                searchInput.parentElement.appendChild(box);
                            }
                            if (data.length > 0) {
                                box.innerHTML = data.map(d =>
                                    `<a href="/listing/${d.id}" style="display:block;padding:10px 16px;color:var(--text-primary);border-bottom:1px solid var(--border);font-size:0.9rem;">
                                        <strong>${d.title}</strong>
                                        <span style="color:var(--text-muted);font-size:0.8rem;"> — ${d.material || ''} — ${d.city || ''}/${d.state || ''}</span>
                                        <span style="float:right;color:var(--accent);">R$ ${d.price.toLocaleString('pt-BR', {minimumFractionDigits: 2})}</span>
                                    </a>`
                                ).join('');
                                box.style.display = 'block';
                            } else {
                                box.style.display = 'none';
                            }
                        });
                } else {
                    const box = document.getElementById('search-suggestions');
                    if (box) box.style.display = 'none';
                }
            }, 300);
        });
    }

    // Format currency inputs
    document.querySelectorAll('input[type="number"][step="0.01"]').forEach(input => {
        input.addEventListener('blur', function() {
            if (this.value) {
                this.value = parseFloat(this.value).toFixed(2);
            }
        });
    });

    // Mobile menu: controlado só pela classe .mobile-show (toggle no onclick do botão em base.html)

    // Preview de imagens: gerenciado pelo script próprio de listings/create.html (#preview)

    // Auto-scroll chat
    const chatContainer = document.querySelector('.chat-container');
    if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
});

// Format BRL
function formatBRL(value) {
    return new Intl.NumberFormat('pt-BR', {
        style: 'currency',
        currency: 'BRL'
    }).format(value);
}
