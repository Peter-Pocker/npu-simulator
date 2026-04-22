SHELL := /bin/zsh

LATEXMK := /Library/TeX/texbin/latexmk
GS := gs
BUILD_DIR := build

GS_FLAGS := -sDEVICE=pdfwrite -dCompatibilityLevel=1.5 -dPDFSETTINGS=/printer -dNOPAUSE -dQUIET -dBATCH

.PHONY: pdf clean watch compress

pdf:
	@mkdir -p $(BUILD_DIR)
	$(LATEXMK) -xelatex -latexoption="-shell-escape" -interaction=nonstopmode -file-line-error main.tex
	@cp $(BUILD_DIR)/main.pdf main.pdf
	@echo "=> main.pdf updated"

watch:
	@mkdir -p $(BUILD_DIR)
	$(LATEXMK) -pvc -xelatex -latexoption="-shell-escape" -interaction=nonstopmode -file-line-error main.tex

compress: main.pdf
	$(GS) $(GS_FLAGS) -sOutputFile=main_compressed.pdf main.pdf
	@ls -lh main.pdf main_compressed.pdf
	@echo "=> main_compressed.pdf (300dpi)"

clean:
	$(LATEXMK) -C
	@rm -rf $(BUILD_DIR)
	@rm -f main_compressed.pdf
